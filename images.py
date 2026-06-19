"""Image generation and editing via xAI Imagine API."""

from __future__ import annotations

import base64
import mimetypes
import uuid
from pathlib import Path

import httpx
from openai import OpenAI
from PIL import Image

from config import (
    GENERATED_DIR,
    IMAGE_MODEL,
    IMAGE_RESOLUTION,
    MAX_EDIT_IMAGES,
    XAI_BASE_URL,
    get_api_key,
)
from persona import MAX_REFERENCE_IMAGES

MAX_PROMPT_CHARS = 500
MIN_IMAGE_SIDE = 256


def _client() -> OpenAI:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "No xAI API key found. Set XAI_API_KEY or sign in with `grok` first."
        )
    return OpenAI(api_key=api_key, base_url=XAI_BASE_URL, timeout=httpx.Timeout(180.0))


def _to_data_uri(image_path: str) -> str:
    path = Path(image_path)
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def _save_b64_image(b64_data: str, prefix: str = "gen") -> str:
    out_path = GENERATED_DIR / f"{prefix}_{uuid.uuid4().hex[:10]}.png"
    out_path.write_bytes(base64.b64decode(b64_data))
    return str(out_path.resolve())


def _download_url_image(url: str, prefix: str = "dl") -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    with httpx.Client(follow_redirects=True, timeout=60.0, headers=headers) as http:
        response = http.get(url)
        response.raise_for_status()
        out_path = GENERATED_DIR / f"{prefix}_{uuid.uuid4().hex[:10]}.png"
        out_path.write_bytes(response.content)
        return str(out_path.resolve())


def is_image_usable(image_path: str) -> bool:
    """Reject blank, black, or corrupt images returned by the API."""
    path = Path(image_path)
    if not path.exists() or path.stat().st_size < 20_000:
        return False

    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            pixels = list(rgb.getdata())
            if not pixels:
                return False

            total = len(pixels)
            dark = sum(1 for r, g, b in pixels if r + g + b < 30)
            avg = sum(r + g + b for r, g, b in pixels) / (total * 3)

            # All-black or nearly all-black outputs
            if dark / total > 0.92 or avg < 12:
                return False
            return True
    except OSError:
        return False


def prepare_reference_image(source_path: str) -> str:
    """
    Normalize a reference for the edit API: valid RGB, reasonable size, saved as PNG.
    Returns path to prepared file (may be a new copy in GENERATED_DIR).
    """
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"Reference not found: {source_path}")

    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        if max(w, h) < MIN_IMAGE_SIDE:
            scale = MIN_IMAGE_SIDE / max(w, h)
            im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        elif max(w, h) > 2048:
            scale = 2048 / max(w, h)
            im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

        out = GENERATED_DIR / f"ref_{uuid.uuid4().hex[:10]}.png"
        im.save(out, format="PNG", optimize=True)
        return str(out.resolve())


def _trim_description(description: str, limit: int = MAX_PROMPT_CHARS) -> str:
    text = " ".join(description.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rsplit(" ", 1)[0] + "..."


def generate_image(
    prompt: str,
    aspect_ratio: str = "1:1",
    resolution: str = "1k",
    n: int = 1,
) -> tuple[str, str]:
    """Generate a new image. Returns (local_path, status_message)."""
    prompt = prompt[:MAX_PROMPT_CHARS * 2]
    client = _client()
    extra: dict = {"aspect_ratio": aspect_ratio, "resolution": resolution}
    if n > 1:
        extra["n"] = min(n, 4)

    response = client.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        response_format="b64_json",
        extra_body=extra,
    )

    best_path: str | None = None
    for i, item in enumerate(response.data or []):
        b64 = item.b64_json
        candidate: str | None = None
        if b64:
            candidate = _save_b64_image(b64, prefix="gen")
        elif item.url:
            candidate = _download_url_image(item.url, prefix="gen")

        if candidate and is_image_usable(candidate):
            best_path = candidate
            if i == 0:
                break

        if candidate and not is_image_usable(candidate):
            Path(candidate).unlink(missing_ok=True)

    if best_path:
        return best_path, "Image generated successfully."

    raise RuntimeError("Generated image was blank or black.")


def _parse_edit_response(data: dict, prefix: str = "edit") -> tuple[str, str]:
    item = data.get("data", [{}])[0]
    b64 = item.get("b64_json")
    if b64:
        path = _save_b64_image(b64, prefix=prefix)
        if not is_image_usable(path):
            Path(path).unlink(missing_ok=True)
            raise RuntimeError("Edited image was blank or black.")
        return path, "Image edited successfully."

    url = item.get("url")
    if url:
        path = _download_url_image(url, prefix=prefix)
        if not is_image_usable(path):
            Path(path).unlink(missing_ok=True)
            raise RuntimeError("Edited image was blank or black.")
        return path, "Image edited and downloaded."

    raise RuntimeError("No edited image returned from API.")


def _api_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        err = body.get("error", body.get("message", body))
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or err)
        if err:
            return str(err)
    except Exception:
        pass
    text = (response.text or "").strip()
    return text[:300] if text else response.reason_phrase


def _post_image_edit(payload: dict) -> tuple[str, str]:
    with httpx.Client(timeout=180.0) as http:
        api_key = get_api_key()
        if not api_key:
            raise RuntimeError("No xAI API key found. Set XAI_API_KEY or sign in with `grok`.")

        response = http.post(
            f"{XAI_BASE_URL}/images/edits",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code >= 400:
            detail = _api_error_detail(response)
            raise RuntimeError(f"Image edit failed ({response.status_code}): {detail}")
        return _parse_edit_response(response.json())


def edit_image(
    source_path: str,
    prompt: str,
    aspect_ratio: str = "auto",
    resolution: str | None = None,
) -> tuple[str, str]:
    """Edit an existing image. Returns (local_path, status_message)."""
    prepared = prepare_reference_image(source_path)
    data_uri = _to_data_uri(prepared)
    prompt = prompt[:MAX_PROMPT_CHARS * 2]

    payload: dict = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "image": {"url": data_uri, "type": "image_url"},
        "response_format": "b64_json",
        "resolution": resolution or IMAGE_RESOLUTION,
    }
    if aspect_ratio != "auto":
        payload["aspect_ratio"] = aspect_ratio

    return _post_image_edit(payload)


def edit_image_multi(
    source_paths: list[str],
    prompt: str,
    aspect_ratio: str = "1:1",
    resolution: str | None = None,
) -> tuple[str, str]:
    """
    Edit using up to 3 reference images (xAI multi-image API).
    Reference images in the prompt as <IMAGE_0>, <IMAGE_1>, <IMAGE_2>.
    """
    paths = [p for p in source_paths if p and Path(p).exists()][:MAX_EDIT_IMAGES]
    if not paths:
        raise ValueError("No source images for multi-image edit.")
    if len(paths) == 1:
        return edit_image(paths[0], prompt, aspect_ratio=aspect_ratio, resolution=resolution)

    prepared = [prepare_reference_image(p) for p in paths]
    images_payload = [{"url": _to_data_uri(p), "type": "image_url"} for p in prepared]

    payload: dict = {
        "model": IMAGE_MODEL,
        "prompt": prompt[: MAX_PROMPT_CHARS * 2],
        "images": images_payload,
        "response_format": "b64_json",
        "resolution": resolution or IMAGE_RESOLUTION,
        "aspect_ratio": aspect_ratio if aspect_ratio != "auto" else "1:1",
    }
    path, msg = _post_image_edit(payload)
    return path, f"Multi-reference image created ({len(paths)} refs). {msg}"


def _style_bits(persona_type: str) -> str:
    return {
        "anime": "anime art style, vibrant expressive colors, clean linework",
        "real": "photorealistic phone selfie, natural skin texture, high detail",
        "fictional": "cinematic character portrait, dramatic lighting, rich detail",
    }.get(persona_type, "high quality character portrait")


def pick_reference_images(
    character_refs: list[str],
    user_images: list[str] | None = None,
    max_images: int = MAX_EDIT_IMAGES,
) -> list[tuple[str, str]]:
    """
    Pick a spread of references — not only the latest upload.
    Returns (path, role) pairs for multi-image editing.
    """
    refs = [p for p in character_refs if p and Path(p).exists()]
    users = [p for p in (user_images or []) if p and Path(p).exists()]
    picks: list[tuple[str, str]] = []
    used: set[str] = set()

    def add(path: str, role: str) -> None:
        if path not in used and len(picks) < max_images:
            picks.append((path, role))
            used.add(path)

    if refs:
        add(refs[0], "character_primary")
        if len(refs) > 2:
            add(refs[len(refs) // 2], "character_ref")
        if len(refs) > 1:
            add(refs[-1], "character_ref")

    if users:
        add(users[-1], "user_item")

    for ref in refs[1:]:
        add(ref, "character_ref")

    return picks[:max_images]


def build_multi_image_edit_prompt(
    profile: object,
    user_message: str,
    image_roles: list[tuple[str, str]],
) -> str:
    """Prompt for xAI multi-image edit using <IMAGE_N> tokens."""
    name = getattr(profile, "name", "subject")
    persona_type = getattr(profile, "persona_type", "real")
    desc = _trim_description(getattr(profile, "description", "") or "", limit=200)

    role_lines: list[str] = []
    for i, (_, role) in enumerate(image_roles):
        if role == "character_primary":
            role_lines.append(
                f"<IMAGE_{i}> is {name} — primary face and body reference. Keep exact likeness."
            )
        elif role == "character_ref":
            role_lines.append(
                f"<IMAGE_{i}> is another photo of {name} — use for face, hair, build, and style accuracy."
            )
        elif role == "user_item":
            role_lines.append(
                f"<IMAGE_{i}> is from the user — wear it, hold it, or recreate it in the new scene."
            )

    request = user_message.strip()[:220] if user_message else "casual selfie looking at camera"
    bits = [
        "Create a BRAND NEW image — fresh pose, new background, new composition.",
        "Do NOT just paste a filter on the reference. Generate an original scene.",
        " ".join(role_lines),
        f"Scene: {request}.",
    ]
    if desc:
        bits.append(desc)
    bits.append(f"{_style_bits(persona_type)}, bright vivid lighting, colorful, not dark or black.")
    return " ".join(bits)


def build_selfie_edit_prompt(profile: object, user_message: str = "") -> str:
    """Focused prompt for single-reference edits — creates a new scene, not a clone."""
    name = getattr(profile, "name", "subject")
    persona_type = getattr(profile, "persona_type", "real")
    desc = _trim_description(getattr(profile, "description", "") or "", limit=180)
    request = user_message.strip()[:200] if user_message else "casual selfie, looking at camera"

    bits = [
        f"Create a NEW photorealistic image of the same person as the reference — {name}.",
        "New pose, new setting, original composition — not a copy of the reference background.",
        f"Scene: {request}.",
        f"{_style_bits(persona_type)}, bright clear lighting, colorful, not dark.",
        "Keep exact face, hair, skin tone, and body likeness from the reference.",
    ]
    if desc:
        bits.insert(2, desc)
    return " ".join(bits)


def build_selfie_generate_prompt(profile: object, user_message: str = "") -> str:
    """Rich generate prompt — creates from scratch using persona details."""
    name = getattr(profile, "name", "character")
    persona_type = getattr(profile, "persona_type", "anime")
    desc = _trim_description(getattr(profile, "description", "") or "", limit=350)
    personality = _trim_description(getattr(profile, "personality", "") or "", limit=120)
    request = user_message.strip()[:220] if user_message else "selfie portrait, looking at camera"

    parts = [
        f"{name}, {request}",
        "Original newly generated image, not an edit.",
        _style_bits(persona_type),
    ]
    if desc:
        parts.append(desc)
    if personality:
        parts.append(f"Expression and vibe: {personality}")
    parts.append("Bright vivid lighting, sharp focus, colorful, not dark or black.")
    return ". ".join(parts)[: MAX_PROMPT_CHARS * 2]


def generate_character_image(
    profile: object,
    user_request: str,
    user_images: list[str] | None = None,
    aspect_ratio: str = "1:1",
) -> tuple[str | None, str]:
    """Delegate to the smart image pipeline (generate-first, rotation, clone rejection)."""
    from image_engine import run_image_pipeline

    return run_image_pipeline(profile, user_request, user_images, aspect_ratio)


def build_selfie_prompt(profile: object, user_message: str = "") -> str:
    """Legacy alias used by chat flow."""
    return build_selfie_edit_prompt(profile, user_message)


def build_image_prompt(
    character_name: str,
    persona_type: str,
    user_request: str,
    description: str = "",
    use_reference: bool = False,
) -> str:
    """Craft an image prompt aligned with persona type and safety rules."""
    if use_reference:
        # Reference edits should stay short
        return user_request[:MAX_PROMPT_CHARS * 2]

    style_bits = {
        "anime": "anime art style, expressive eyes, clean linework, vibrant colors",
        "real": "photorealistic portrait, natural lighting, high detail",
        "fictional": "cinematic character portrait, detailed, atmospheric",
    }.get(persona_type, "high quality character portrait")

    short_desc = _trim_description(description, limit=250)
    parts = [character_name]
    if short_desc:
        parts.append(short_desc)
    parts.append(user_request)
    parts.append(style_bits)
    parts.append("bright clear lighting, not dark or black")

    return ". ".join(p for p in parts if p)[: MAX_PROMPT_CHARS * 2]