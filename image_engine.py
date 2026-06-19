"""
Smart image pipeline — fast, early-exit, multi-ref rotation.

Runs strategies one at a time and returns on the first good result
instead of firing every API call before picking a winner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
from openai import OpenAI
from PIL import Image

from config import (
    CHAT_MODEL,
    IMAGE_CLONE_THRESHOLD,
    IMAGE_CRAFT_PROMPTS,
    IMAGE_GENERATE_FIRST,
    IMAGE_MAX_MULTI_SLOTS,
    IMAGE_MAX_SINGLE_TRIES,
    IMAGE_RESOLUTION,
    IMAGE_STATE_DIR,
    MAX_EDIT_IMAGES,
    XAI_BASE_URL,
    get_api_key,
)
from images import (
    _trim_description,
    build_multi_image_edit_prompt,
    build_selfie_edit_prompt,
    build_selfie_generate_prompt,
    edit_image,
    edit_image_multi,
    generate_image,
    is_image_usable,
)
from persona import MAX_REFERENCE_IMAGES

MAX_PROMPT_CHARS = 800


@dataclass
class ImageAttempt:
    strategy: str
    path: str | None = None
    message: str = ""
    refs_used: list[int] = field(default_factory=list)
    similarity: float = 0.0


@dataclass
class RotationState:
    persona_id: str
    rotation_index: int = 0
    last_strategy: str = ""
    last_refs_used: list[int] = field(default_factory=list)
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class _PromptCache:
    generate: str = ""
    multi_edit: str = ""
    single_edit: str = ""


def _rotation_path(persona_id: str) -> Path:
    return IMAGE_STATE_DIR / f"{persona_id}.json"


def load_rotation(persona_id: str) -> RotationState:
    path = _rotation_path(persona_id)
    if not path.exists():
        return RotationState(persona_id=persona_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RotationState(
            persona_id=persona_id,
            rotation_index=int(data.get("rotation_index", 0)),
            last_strategy=data.get("last_strategy", ""),
            last_refs_used=data.get("last_refs_used", []),
        )
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return RotationState(persona_id=persona_id)


def save_rotation(state: RotationState) -> None:
    state.updated_at = datetime.now(timezone.utc).isoformat()
    _rotation_path(state.persona_id).write_text(
        json.dumps(
            {
                "persona_id": state.persona_id,
                "rotation_index": state.rotation_index,
                "last_strategy": state.last_strategy,
                "last_refs_used": state.last_refs_used,
                "updated_at": state.updated_at,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def resolve_reference_paths(profile: object) -> list[str]:
    seen: set[str] = set()
    resolved: list[str] = []
    for raw in getattr(profile, "reference_images", []) or []:
        if not raw:
            continue
        try:
            path = str(Path(raw).resolve())
        except OSError:
            continue
        if path in seen or not Path(path).exists():
            continue
        seen.add(path)
        resolved.append(path)
    return resolved


def rotate_list(items: list[str], start: int) -> list[str]:
    if not items:
        return []
    start = start % len(items)
    return items[start:] + items[:start]


def image_similarity(output_path: str, source_path: str) -> float:
    try:
        with Image.open(output_path) as out, Image.open(source_path) as src:
            size = (128, 128)
            out_px = list(out.convert("RGB").resize(size).getdata())
            src_px = list(src.convert("RGB").resize(size).getdata())
            if not out_px:
                return 0.0
            total_diff = sum(
                abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
                for (r1, g1, b1), (r2, g2, b2) in zip(out_px, src_px)
            )
            return 1.0 - (total_diff / (255 * 3 * len(out_px)))
    except OSError:
        return 0.0


def similarity_to_sources(output_path: str, sources: list[str]) -> tuple[float, int]:
    best = 0.0
    best_idx = -1
    for i, src in enumerate(sources):
        sim = image_similarity(output_path, src)
        if sim > best:
            best = sim
            best_idx = i
    return best, best_idx


def _llm_client() -> OpenAI:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("No xAI API key found.")
    return OpenAI(api_key=api_key, base_url=XAI_BASE_URL, timeout=httpx.Timeout(60.0))


def _craft_prompt_once(
    cache: _PromptCache,
    profile: object,
    user_request: str,
    mode: str,
) -> str:
    if mode == "generate" and cache.generate:
        return cache.generate
    if mode == "multi_edit" and cache.multi_edit:
        return cache.multi_edit
    if mode == "single_edit" and cache.single_edit:
        return cache.single_edit

    if not IMAGE_CRAFT_PROMPTS:
        prompt = (
            build_selfie_generate_prompt(profile, user_request)
            if mode == "generate"
            else build_selfie_edit_prompt(profile, user_request)
        )
    else:
        name = getattr(profile, "name", "character")
        desc = _trim_description(getattr(profile, "description", "") or "", limit=400)
        persona_type = getattr(profile, "persona_type", "real")
        try:
            client = _llm_client()
            response = client.chat.completions.create(
                model=CHAT_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Write a short Grok Imagine image prompt. "
                            "Output ONLY the prompt."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"{name} ({persona_type}). {desc}\n"
                            f"Request: {user_request}\n"
                            f"Mode: {mode}. New scene, bright lighting."
                        ),
                    },
                ],
                temperature=0.6,
                max_tokens=300,
            )
            prompt = (response.choices[0].message.content or "").strip()
            if len(prompt) < 20:
                raise ValueError("prompt too short")
        except Exception:
            prompt = (
                build_selfie_generate_prompt(profile, user_request)
                if mode == "generate"
                else build_selfie_edit_prompt(profile, user_request)
            )

    if mode == "generate":
        cache.generate = prompt
    elif mode == "multi_edit":
        cache.multi_edit = prompt
    else:
        cache.single_edit = prompt
    return prompt


def build_rotated_image_set(
    refs: list[str],
    rotation_index: int,
    user_images: list[str] | None,
    slot: int = 0,
) -> list[tuple[str, str, int]]:
    rotated = rotate_list(refs, (rotation_index + slot) % max(len(refs), 1))
    picks: list[tuple[str, str, int]] = []
    used: set[str] = set()
    ref_index_map = {p: i for i, p in enumerate(refs)}

    def add(path: str, role: str, idx: int) -> None:
        if path not in used and len(picks) < MAX_EDIT_IMAGES:
            picks.append((path, role, idx))
            used.add(path)

    if rotated:
        add(rotated[0], "character_primary", ref_index_map.get(rotated[0], 0))
    if len(rotated) > 1:
        add(rotated[1], "character_ref", ref_index_map.get(rotated[1], 1))
    if len(rotated) > 2:
        add(rotated[2], "character_ref", ref_index_map.get(rotated[2], 2))

    users = [p for p in (user_images or []) if p and Path(p).exists()]
    if users:
        user_path = users[-1]
        if user_path not in used:
            if len(picks) >= MAX_EDIT_IMAGES:
                picks[-1] = (user_path, "user_item", -1)
            else:
                picks.append((user_path, "user_item", -1))

    return picks


def _clone_check_sources(attempt: ImageAttempt, refs: list[str]) -> list[str]:
    """Only compare to the refs actually used — not every uploaded photo."""
    if attempt.refs_used:
        return [refs[i] for i in attempt.refs_used if 0 <= i < len(refs)]
    return []


def _accept_attempt(
    attempt: ImageAttempt,
    refs: list[str],
    *,
    strict: bool = True,
) -> tuple[str, str] | None:
    if not attempt.path or not is_image_usable(attempt.path):
        return None

    check_sources = _clone_check_sources(attempt, refs)
    if not check_sources:
        check_sources = refs[:1]

    sim, clone_idx = similarity_to_sources(attempt.path, check_sources)
    attempt.similarity = sim

    threshold = IMAGE_CLONE_THRESHOLD
    if attempt.strategy == "generate":
        threshold = min(0.98, threshold + 0.04)

    if strict and sim >= threshold:
        attempt.message += f" (rejected: {sim:.0%} similar to ref #{clone_idx + 1})"
        return None

    ref_label = (
        ", ".join(f"#{i + 1}" for i in attempt.refs_used) if attempt.refs_used else "generated"
    )
    msg = f"{attempt.message} [strategy: {attempt.strategy}, refs: {ref_label}]"
    return str(Path(attempt.path).resolve()), msg


def _try_generate(profile: object, user_request: str, cache: _PromptCache) -> ImageAttempt:
    prompt = _craft_prompt_once(cache, profile, user_request, "generate")
    try:
        path, msg = generate_image(prompt, aspect_ratio="1:1", resolution=IMAGE_RESOLUTION)
        return ImageAttempt(strategy="generate", path=path, message=msg)
    except Exception as exc:
        return ImageAttempt(strategy="generate", message=str(exc))


def _try_multi_edit(
    profile: object,
    user_request: str,
    refs: list[str],
    user_images: list[str] | None,
    rotation_index: int,
    slot: int,
    cache: _PromptCache,
) -> ImageAttempt:
    image_set = build_rotated_image_set(refs, rotation_index, user_images, slot=slot)
    if len(image_set) < 2:
        return ImageAttempt(strategy="multi_edit", message="not enough refs")

    roles = [(p, role) for p, role, _ in image_set]
    indices = [idx for _, _, idx in image_set if idx >= 0]
    prompt = _craft_prompt_once(cache, profile, user_request, "multi_edit")
    if "<IMAGE_0>" not in prompt:
        prompt = build_multi_image_edit_prompt(profile, user_request, roles)

    try:
        paths = [p for p, _, _ in image_set]
        path, msg = edit_image_multi(paths, prompt, aspect_ratio="1:1")
        return ImageAttempt(strategy="multi_edit", path=path, message=msg, refs_used=indices)
    except Exception as exc:
        return ImageAttempt(strategy="multi_edit", message=str(exc), refs_used=indices)


def _try_single_edit(
    profile: object,
    user_request: str,
    refs: list[str],
    rotation_index: int,
    offset: int,
    cache: _PromptCache,
) -> ImageAttempt:
    if not refs:
        return ImageAttempt(strategy="single_edit", message="no refs")
    idx = (rotation_index + offset) % len(refs)
    source = refs[idx]
    prompt = _craft_prompt_once(cache, profile, user_request, "single_edit")
    try:
        path, msg = edit_image(source, prompt, aspect_ratio="1:1")
        return ImageAttempt(strategy="single_edit", path=path, message=msg, refs_used=[idx])
    except Exception as exc:
        return ImageAttempt(strategy="single_edit", message=str(exc), refs_used=[idx])


def _is_access_denied(message: str) -> bool:
    lower = (message or "").lower()
    return "403" in lower or "forbidden" in lower


def _record_success(
    rotation: RotationState,
    refs: list[str],
    attempt: ImageAttempt,
    path: str,
    msg: str,
) -> tuple[str, str]:
    rotation.rotation_index = (rotation.rotation_index + 1) % max(len(refs), 1)
    rotation.last_strategy = attempt.strategy
    rotation.last_refs_used = attempt.refs_used
    save_rotation(rotation)
    return path, msg


def run_image_pipeline(
    profile: object,
    user_request: str,
    user_images: list[str] | None = None,
    aspect_ratio: str = "1:1",
) -> tuple[str | None, str]:
    """
    Run image strategies one-by-one; return immediately on first success.
    Real personas: edit-first (generate usually fails). Anime/fictional: generate-first.
    """
    persona_id = getattr(profile, "id", "unknown")
    name = getattr(profile, "name", "character")
    persona_type = getattr(profile, "persona_type", "real")
    refs = resolve_reference_paths(profile)
    rotation = load_rotation(persona_id)
    cache = _PromptCache()
    errors: list[str] = []
    best_fallback: ImageAttempt | None = None
    best_sim = 1.0
    edits_blocked = False

    if persona_type == "real" and not refs:
        return None, (
            f"Upload reference photos for {name} first "
            f"(1–{MAX_REFERENCE_IMAGES} clear face photos)."
        )

    def track_fallback(attempt: ImageAttempt) -> None:
        nonlocal best_fallback, best_sim
        if not attempt.path or not is_image_usable(attempt.path):
            return
        check = _clone_check_sources(attempt, refs) or refs[:1]
        sim, _ = similarity_to_sources(attempt.path, check)
        if sim < best_sim:
            best_sim = sim
            best_fallback = attempt

    def try_and_return(fn: Callable[[], ImageAttempt], *, strict: bool = True) -> tuple[str, str] | None:
        nonlocal edits_blocked
        attempt = fn()
        result = _accept_attempt(attempt, refs, strict=strict)
        if result:
            return _record_success(rotation, refs, attempt, *result)
        track_fallback(attempt)
        if attempt.message:
            errors.append(f"{attempt.strategy}: {attempt.message}")
            if attempt.strategy != "generate" and _is_access_denied(attempt.message):
                edits_blocked = True
        return None

    # Build ordered strategy queue — edit-first for real people
    edit_first = persona_type == "real" or len(refs) >= 2

    if edit_first:
        if not edits_blocked and len(refs) >= 2:
            for slot in range(min(len(refs), IMAGE_MAX_MULTI_SLOTS)):
                hit = try_and_return(
                    lambda s=slot: _try_multi_edit(
                        profile, user_request, refs, user_images, rotation.rotation_index, s, cache
                    )
                )
                if hit:
                    return hit
                if edits_blocked:
                    break
        elif not edits_blocked and len(refs) == 1 and user_images:
            hit = try_and_return(
                lambda: _try_multi_edit(
                    profile, user_request, refs, user_images, rotation.rotation_index, 0, cache
                )
            )
            if hit:
                return hit

        if not edits_blocked:
            tries = min(len(refs), IMAGE_MAX_SINGLE_TRIES)
            for offset in range(tries):
                hit = try_and_return(
                    lambda o=offset: _try_single_edit(
                        profile, user_request, refs, rotation.rotation_index, o, cache
                    )
                )
                if hit:
                    return hit
                if edits_blocked:
                    break

        if IMAGE_GENERATE_FIRST or edits_blocked:
            hit = try_and_return(
                lambda: _try_generate(profile, user_request, cache),
                strict=not edits_blocked,
            )
            if hit:
                return hit
    else:
        if IMAGE_GENERATE_FIRST:
            hit = try_and_return(lambda: _try_generate(profile, user_request, cache))
            if hit:
                return hit

        if len(refs) >= 2:
            for slot in range(min(len(refs), IMAGE_MAX_MULTI_SLOTS)):
                hit = try_and_return(
                    lambda s=slot: _try_multi_edit(
                        profile, user_request, refs, user_images, rotation.rotation_index, s, cache
                    )
                )
                if hit:
                    return hit

        tries = min(len(refs), IMAGE_MAX_SINGLE_TRIES)
        for offset in range(tries):
            hit = try_and_return(
                lambda o=offset: _try_single_edit(
                    profile, user_request, refs, rotation.rotation_index, o, cache
                )
            )
            if hit:
                return hit

    # Relaxed pass — accept first usable even if somewhat similar
    if best_fallback and best_fallback.path:
        result = _accept_attempt(best_fallback, refs, strict=False)
        if result:
            return _record_success(
                rotation,
                refs,
                best_fallback,
                *result,
            )

    rotation.rotation_index = (rotation.rotation_index + 1) % max(len(refs), 1)
    save_rotation(rotation)

    detail = "; ".join(errors[:3]) if errors else "all strategies failed"
    return None, f"Could not create image. {detail}"