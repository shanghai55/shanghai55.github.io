"""In-character chat powered by xAI — single and multi-character."""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from openai import OpenAI

from config import (
    CHAT_ENHANCE,
    CHAT_MAX_TOKENS,
    CHAT_MODEL,
    CHAT_TEMPERATURE,
    SUGGESTED_REPLIES,
    XAI_BASE_URL,
    get_api_key,
)
from image_engine import run_image_pipeline
from memory import build_memory_context
from persona import (
    MAX_REFERENCE_IMAGES,
    PersonaProfile,
    add_to_image_history,
    build_system_prompt,
)
from search import format_research_summary, search_web


@dataclass
class ChatSession:
    persona_id: str
    messages: list[dict[str, str]] = field(default_factory=list)


PHOTO_REQUEST_PATTERNS = [
    r"send\s+(me\s+)?(a\s+)?(photo|pic|picture|selfie|image|snap)\b",
    r"send\s+(ka|mo)\s+.*(photo|pic|picture|selfie|larawan|image)",
    r"send\s+(ka|mo)\b",
    r"(photo|pic|picture|selfie|image|larawan)\s+(of\s+)?(yourself|your\s*self|u|you|mo|niyo|na)",
    r"(photo|pic|picture|selfie|larawan).*(send|padala|bigay|show|kuha)",
    r"(send|padala|bigay|show|kuha).*(photo|pic|picture|selfie|larawan|image)",
    r"padala.*(larawan|pic|photo|selfie)",
    r"selfie\s*(naman|po|pls|please)?",
    r"pic\s*(mo|naman|po)?",
    r"larawan\s*mo",
    r"picture\s*(mo|please|naman|po|pls|na)?",
    r"show\s+(me\s+)?(what\s+you\s+look\s+like|your\s+face|yourself|you)",
    r"let\s+me\s+see\s+(you|your\s+face|yourself)",
    r"makita\s+(kita|mukha\s+mo|ko|mo)",
    r"(can|could)\s+you\s+send.*(pic|photo|picture|selfie)",
    r"(give|gimme|give\s+me).*(pic|photo|picture|selfie)",
    r"gawin\s+mo\s*(nga)?",
    r"(suot|wear).*(photo|pic|picture|selfie|yan|sya|ito)",
    r"(photo|pic|picture|selfie).*(suot|wear)",
    r"\bselfie\b",
    r"\bselfing\b",
    r"\bselpie\b",
]

PHOTO_KEYWORDS = re.compile(
    r"\b(photo|photos|pic|pics|picture|pictures|selfie|selpie|selfing|selfy|larawan|image|snap)\b",
    re.I,
)
ACTION_KEYWORDS = re.compile(
    r"\b(send|show|give|padala|bigay|kuha|share|see|makita|gawin|gusto|want|need|please|naman|po|pls|suot|wear)\b",
    re.I,
)
SELF_KEYWORDS = re.compile(
    r"\b(yourself|your\s*self|you|u|ur|mo|niyo|face|mukha|itsura|baba|katawan|body)\b",
    re.I,
)
VISUAL_REQUEST = re.compile(
    r"\b(makita|see|show|tingnan|gawin\s+mo|wala.*(pants|shirt|shorts|clothes)|"
    r"without\s+(pants|shirt|shorts|clothes)|underwear|topless|flex)\b",
    re.I,
)
PHOTO_CLAIM_PATTERNS = [
    r"\[sent a photo\]",
    r"\[shared an image\]",
    r"\[shared a photo\]",
    r"sent a photo",
    r"shared (a |an )?(photo|image|pic)",
    r"eto na.*📸",
    r"here.*photo",
    r"tingnan mo.*bagay",
]


def _client() -> OpenAI:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "No xAI API key found. Set XAI_API_KEY or sign in with `grok` first."
        )
    return OpenAI(api_key=api_key, base_url=XAI_BASE_URL, timeout=httpx.Timeout(120.0))


def is_photo_request(message: str, *, has_user_images: bool = False) -> bool:
    text = message.lower().strip()
    if not text:
        return False

    if any(re.search(pattern, text, re.IGNORECASE) for pattern in PHOTO_REQUEST_PATTERNS):
        return True

    if has_user_images and PHOTO_KEYWORDS.search(text) and ACTION_KEYWORDS.search(text):
        return True

    if has_user_images and re.search(r"\b(suot|wear)\b", text, re.I):
        return True

    has_photo = bool(PHOTO_KEYWORDS.search(text))
    if has_photo and (ACTION_KEYWORDS.search(text) or SELF_KEYWORDS.search(text)):
        return True

    if VISUAL_REQUEST.search(text) and (
        SELF_KEYWORDS.search(text) or has_photo or ACTION_KEYWORDS.search(text)
    ):
        return True

    # Short blunt requests: "selfie", "pic?", "photo"
    if has_photo and len(text.split()) <= 4:
        return True

    return False


def _reply_claims_photo_sent(text: str) -> bool:
    if not text:
        return False
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in PHOTO_CLAIM_PATTERNS)


def detect_target_persona(
    message: str,
    active_profiles: list[PersonaProfile],
    default_id: str | None,
) -> PersonaProfile | None:
    """Route @mentions or name mentions to the right character."""
    if not active_profiles:
        return None

    text = message.lower()
    for profile in active_profiles:
        name = profile.name.lower()
        if f"@{name}" in text or re.search(rf"\b{re.escape(name)}\b", text):
            return profile

    if default_id:
        for profile in active_profiles:
            if profile.id == default_id:
                return profile

    return active_profiles[0] if len(active_profiles) == 1 else None


def fulfill_photo_request(
    profile: PersonaProfile,
    user_message: str,
    user_images: list[str] | None = None,
) -> tuple[str | None, str]:
    """
    Generate or edit a character image using all references + optional user photos.
    Returns (image_path, status_message).
    """
    return run_image_pipeline(
        profile,
        user_message,
        user_images=user_images,
        aspect_ratio="1:1",
    )


def _image_to_data_uri(image_path: str) -> str:
    path = Path(image_path)
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def _build_user_api_content(text: str, image_paths: list[str] | None = None) -> str | list:
    """OpenAI/xAI multimodal user message for vision."""
    images = [p for p in (image_paths or []) if p and Path(p).exists()]
    if not images:
        return text

    parts: list[dict] = []
    if text.strip():
        parts.append({"type": "text", "text": text})
    else:
        parts.append(
            {
                "type": "text",
                "text": (
                    "The user sent you a photo. Look at it carefully and respond in character "
                    "to what you see — comment naturally like you're chatting."
                ),
            }
        )

    for img in images[:3]:
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": _image_to_data_uri(img), "detail": "high"},
            }
        )
    return parts


def _build_user_display_content(text: str, image_paths: list[str] | None = None):
    """Gradio chat display for user messages with optional images."""
    images = [p for p in (image_paths or []) if p and Path(p).exists()]
    if not images:
        return text

    items: list = []
    if text.strip():
        items.append(text)
    for img in images:
        path = Path(img).resolve()
        mime, _ = mimetypes.guess_type(path.name)
        items.append({"path": str(path), "mime_type": mime or "image/png"})
    return items


def _content_to_text(content, *, image_placeholder: str = "[shared an image]") -> str:
    """Extract plain text from Gradio chat message content."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            if item.get("type") == "text" and item.get("text"):
                parts.append(item["text"])
            elif item.get("path"):
                parts.append(image_placeholder)
            elif item.get("type") == "file" and item.get("file", {}).get("path"):
                parts.append(image_placeholder)
    return " ".join(parts).strip()


def _strip_speaker_prefix(text: str, name: str) -> str:
    """Remove leaked [Name]: labels the model copies from API history."""
    cleaned = text.strip()
    for prefix in (f"[{name}]:", f"**{name}:**", f"{name}:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].lstrip()
    return cleaned


def _strip_photo_placeholders(text: str) -> str:
    """Remove placeholder phrases that imply a photo was attached."""
    cleaned = text
    for pattern in (
        r"\[sent a photo\]",
        r"\[shared an image\]",
        r"\[shared a photo\]",
        r"\s*sent a photo\s*",
        r"\s*shared (?:a |an )?(?:photo|image|pic)\s*",
    ):
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[ \t]*\n[ \t]*\n[ \t]*", "\n\n", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _format_history_for_api(
    history: list[dict],
    responding_as: str,
) -> list[dict]:
    """Convert display history to API messages with speaker labels and vision."""
    api_messages: list[dict] = []
    for entry in history:
        role = entry.get("role", "user")
        speaker = entry.get("speaker", "")
        raw = entry.get("content", "")

        if role == "user":
            if isinstance(raw, list):
                text_parts: list[str] = []
                image_paths: list[str] = []
                for item in raw:
                    if isinstance(item, str):
                        text_parts.append(item)
                    elif isinstance(item, dict) and item.get("path"):
                        image_paths.append(item["path"])
                text = " ".join(text_parts).strip()
                api_messages.append(
                    {"role": "user", "content": _build_user_api_content(text, image_paths)}
                )
            else:
                api_messages.append({"role": "user", "content": _content_to_text(raw)})
        else:
            text = _content_to_text(raw, image_placeholder="")
            if isinstance(raw, list) and any(
                isinstance(item, dict) and item.get("path") for item in raw
            ):
                text = f"{text} (shared a photo earlier)".strip()
            api_messages.append({"role": "assistant", "content": text})
    return api_messages


def _build_assistant_content(reply: str, image_path: str | None, multi_char: bool, name: str):
    """Build Gradio 6-compatible assistant message content."""
    text = f"**{name}:** {reply}" if multi_char else reply
    if image_path:
        path = Path(image_path).resolve()
        mime, _ = mimetypes.guess_type(path.name)
        return [text, {"path": str(path), "mime_type": mime or "image/png"}]
    return text


def chat(
    profile: PersonaProfile,
    user_message: str,
    history: list[dict],
    use_web_search: bool = False,
    other_characters: list[PersonaProfile] | None = None,
    user_images: list[str] | None = None,
    session_key: str | None = None,
    scene_override: str | None = None,
    skip_user_append: bool = False,
) -> tuple[str, list[dict], str | None, str | None, str | None]:
    """
    Send a message and get an in-character reply.
    Returns (reply_text, updated_history, search_context, image_path, photo_status).
    """
    image_path = None
    photo_status = None
    search_context = None
    augmented_message = user_message

    if use_web_search:
        query = f"{profile.name} {user_message}"
        results = search_web(query, max_results=5)
        search_context = format_research_summary(query, results)
        augmented_message = (
            f"{user_message}\n\n"
            f"[Background context from web — use only if relevant, never cite it:]\n"
            f"{search_context[:2500]}"
        )

    user_images = [p for p in (user_images or []) if p and Path(p).exists()]
    wants_photo = is_photo_request(user_message, has_user_images=bool(user_images))
    if wants_photo:
        image_path, photo_status = fulfill_photo_request(
            profile, user_message, user_images=user_images or None
        )

    if user_images:
        img_note = (
            "[The user sent you a photo in this message. Look at the image and react in character "
            "to what you see. Be natural, comment on it, answer their question about it, or banter.]"
        )
        if wants_photo:
            img_note += (
                " If they asked you to send a photo of yourself wearing or using something from "
                "their image, you will attach a real photo — do not write [sent a photo] or "
                "pretend; just reply with short flirty text."
            )
        augmented_message = f"{augmented_message}\n\n{img_note}" if augmented_message else img_note

    # Photo replies skip the LLM — it often refuses in-character even when the image is ready.
    if wants_photo and image_path:
        reply = _default_photo_reply(profile)
    elif wants_photo and not image_path:
        reply = _photo_failed_reply(profile, photo_status or "unknown error")
    else:
        system_prompt = build_system_prompt(
            profile,
            other_characters=other_characters,
            scene_override=scene_override,
        )
        memory_key = session_key or profile.id
        memory_ctx = build_memory_context(memory_key, history, profile.name)
        if memory_ctx.memory_prompt:
            system_prompt = f"{system_prompt}\n\n{memory_ctx.memory_prompt}"

        history_slice = history[memory_ctx.history_start_index :]
        api_history = _format_history_for_api(history_slice, profile.name)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(api_history)
        messages.append(
            {"role": "user", "content": _build_user_api_content(augmented_message, user_images)}
        )

        client = _client()
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            temperature=CHAT_TEMPERATURE,
            max_tokens=CHAT_MAX_TOKENS,
            top_p=0.95,
        )

        reply = response.choices[0].message.content or ""
        reply = _strip_ai_tells(reply)
        reply = _strip_speaker_prefix(reply, profile.name)
        reply = _strip_incharacter_refusals(reply)
        if CHAT_ENHANCE and reply.strip():
            reply = _enhance_reply(client, profile, user_message, reply)

        if _reply_claims_photo_sent(reply):
            if not image_path:
                image_path, photo_status = fulfill_photo_request(
                    profile, user_message, user_images=user_images or None
                )
            reply = _strip_photo_placeholders(reply)
            if image_path and not reply.strip():
                reply = _default_photo_reply(profile)
            elif not image_path and not reply.strip():
                reply = _photo_failed_reply(profile, photo_status or "unknown error")

    multi_char = bool(other_characters)
    assistant_entry: dict = {
        "role": "assistant",
        "content": _build_assistant_content(reply, image_path, multi_char, profile.name),
        "speaker": profile.name,
    }

    if skip_user_append:
        updated = history + [assistant_entry]
    else:
        user_entry: dict = {
            "role": "user",
            "content": _build_user_display_content(user_message, user_images),
        }
        updated = history + [user_entry, assistant_entry]
    if image_path:
        add_to_image_history(profile, image_path)
    return reply, updated, search_context, image_path, photo_status


def chat_multi(
    active_profiles: list[PersonaProfile],
    user_message: str,
    history: list[dict],
    reply_as_id: str | None = None,
    use_web_search: bool = False,
    user_images: list[str] | None = None,
    session_key: str | None = None,
    scene_override: str | None = None,
    multi_respond: bool = False,
) -> tuple[str, str, list[dict], str | None, str | None, str | None]:
    """
    Multi-character chat. Picks who responds unless reply_as_id is set.
    Returns (speaker_name, reply_text, updated_history, search_context, image_path, photo_status).
    """
    if not active_profiles:
        raise RuntimeError("Add at least one character to the chat.")

    if multi_respond and len(active_profiles) > 1:
        return _chat_multi_all(
            active_profiles=active_profiles,
            user_message=user_message,
            history=history,
            use_web_search=use_web_search,
            user_images=user_images,
            session_key=session_key,
            scene_override=scene_override,
        )

    target = None
    if reply_as_id:
        target = next((p for p in active_profiles if p.id == reply_as_id), None)

    if not target:
        target = detect_target_persona(user_message, active_profiles, reply_as_id)

    if not target:
        names = ", ".join(p.name for p in active_profiles)
        raise RuntimeError(
            f"Multiple characters in chat ({names}). "
            f"Select who replies in the sidebar, or mention them with @Name."
        )

    others = [p for p in active_profiles if p.id != target.id]
    reply, updated, search_ctx, image_path, photo_status = chat(
        profile=target,
        user_message=user_message,
        history=history,
        use_web_search=use_web_search,
        other_characters=others,
        user_images=user_images,
        session_key=session_key,
        scene_override=scene_override,
    )
    return target.name, reply, updated, search_ctx, image_path, photo_status


def _chat_multi_all(
    active_profiles: list[PersonaProfile],
    user_message: str,
    history: list[dict],
    use_web_search: bool = False,
    user_images: list[str] | None = None,
    session_key: str | None = None,
    scene_override: str | None = None,
) -> tuple[str, str, list[dict], str | None, str | None, str | None]:
    """Every active character responds in turn to the same user message."""
    user_images = [p for p in (user_images or []) if p and Path(p).exists()]
    current_history = list(history)
    user_entry: dict = {
        "role": "user",
        "content": _build_user_display_content(user_message, user_images),
    }
    current_history.append(user_entry)

    speakers: list[str] = []
    replies: list[str] = []
    search_ctx: str | None = None
    image_path: str | None = None
    photo_status: str | None = None
    multi_char = len(active_profiles) > 1

    for index, profile in enumerate(active_profiles):
        others = [p for p in active_profiles if p.id != profile.id]
        prompt = user_message
        if index > 0:
            prompt = (
                f"{user_message}\n\n"
                f"[Group chat — {profile.name} responds next. Other characters already replied above. "
                f"Give YOUR unique take in character; do not repeat what they said.]"
            )

        reply, _, ctx, img, status = chat(
            profile=profile,
            user_message=prompt,
            history=current_history[:-1] if index == 0 else current_history,
            use_web_search=use_web_search and index == 0,
            other_characters=others,
            user_images=user_images if index == 0 else None,
            session_key=session_key,
            scene_override=scene_override,
            skip_user_append=index > 0,
        )

        assistant_entry: dict = {
            "role": "assistant",
            "content": _build_assistant_content(reply, img if index == 0 else None, multi_char, profile.name),
            "speaker": profile.name,
        }
        current_history.append(assistant_entry)

        speakers.append(profile.name)
        replies.append(reply)
        if ctx and not search_ctx:
            search_ctx = ctx
        if img and not image_path:
            image_path = img
            photo_status = status
            add_to_image_history(profile, img)

    combined_reply = "\n\n".join(
        f"**{name}:** {text}" for name, text in zip(speakers, replies)
    )
    return ", ".join(speakers), combined_reply, current_history, search_ctx, image_path, photo_status


def regenerate_last_response(
    active_profiles: list[PersonaProfile],
    history: list[dict],
    reply_as_id: str | None = None,
    use_web_search: bool = False,
    session_key: str | None = None,
    scene_override: str | None = None,
) -> tuple[str, str, list[dict], str | None, str | None, str | None]:
    """Re-roll the last assistant message without re-sending the user message."""
    if len(history) < 2:
        raise RuntimeError("Nothing to regenerate — send a message first.")

    trimmed = history[:-1]
    last_user = trimmed[-1] if trimmed and trimmed[-1].get("role") == "user" else None
    if not last_user:
        raise RuntimeError("Could not find the last user message.")

    user_text = _content_to_text(last_user.get("content", ""))
    user_images: list[str] = []
    content = last_user.get("content", "")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("path"):
                user_images.append(item["path"])

    base_history = trimmed[:-1] if last_user else trimmed
    return chat_multi(
        active_profiles=active_profiles,
        user_message=user_text,
        history=base_history,
        reply_as_id=reply_as_id,
        use_web_search=use_web_search,
        user_images=user_images or None,
        session_key=session_key,
        scene_override=scene_override,
    )


def generate_suggested_replies(
    active_profiles: list[PersonaProfile],
    history: list[dict],
    count: int = 3,
) -> list[str]:
    """Generate quick-reply suggestions the user might send next."""
    if not SUGGESTED_REPLIES or not active_profiles:
        return []

    names = ", ".join(p.name for p in active_profiles)
    recent = history[-6:] if history else []
    transcript_lines: list[str] = []
    for entry in recent:
        role = entry.get("role", "user")
        speaker = entry.get("speaker", "")
        text = _content_to_text(entry.get("content", ""))
        if not text:
            continue
        if role == "assistant" and speaker:
            transcript_lines.append(f"{speaker}: {text[:200]}")
        else:
            transcript_lines.append(f"User: {text[:200]}")

    transcript = "\n".join(transcript_lines) or "(conversation just started)"
    profile = active_profiles[0]
    lang_hint = {
        "tagalog": "Suggestions in Tagalog.",
        "taglish": "Suggestions in Taglish.",
        "english": "Suggestions in English.",
    }.get(profile.language_mode, "Match the conversation language.")

    try:
        client = _client()
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You suggest what a user might text next in a chat with {names}. "
                        f"{lang_hint} Short, natural, 1–2 sentences each. "
                        "Output ONLY a numbered list, no explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Recent chat:\n{transcript}\n\n"
                        f"Suggest {count} diverse replies the user could send next."
                    ),
                },
            ],
            temperature=0.9,
            max_tokens=400,
        )
        raw = response.choices[0].message.content or ""
        suggestions: list[str] = []
        for line in raw.splitlines():
            cleaned = re.sub(r"^[\d\.\)\-\*\s]+", "", line).strip()
            if cleaned and len(cleaned) > 3:
                suggestions.append(cleaned)
        return suggestions[:count]
    except Exception:
        return []


def _default_photo_reply(profile: PersonaProfile) -> str:
    mode = profile.language_mode
    if mode == "tagalog":
        return "Sige pre, eto na! 📸"
    if mode == "taglish":
        return "Here, eto na pre! 📸"
    return "Here you go! 📸"


def _photo_failed_reply(profile: PersonaProfile, reason: str) -> str:
    reason = (reason or "unknown error").strip()
    reason_lower = reason.lower()
    has_refs = bool(profile.reference_images)

    if not has_refs:
        if profile.language_mode == "tagalog":
            return (
                "Pasensya pre, di ko ma-send ngayon. Upload muna ng reference photos "
                f"sa sidebar (1–{MAX_REFERENCE_IMAGES} pics), tapos try ulit."
            )
        if profile.language_mode == "taglish":
            return (
                "Sorry pre, can't send yet — upload reference photos sa sidebar first "
                f"(1–{MAX_REFERENCE_IMAGES} pics), then try again."
            )
        return (
            f"Couldn't send a photo yet — upload reference photos in the sidebar first "
            f"(1–{MAX_REFERENCE_IMAGES} clear face pics), then ask again."
        )

    if "403" in reason_lower or "forbidden" in reason_lower:
        if profile.language_mode == "tagalog":
            return (
                "Pasensya pre, na-block ng image API ang edit request (403). "
                "Subukan ulit ng ibang scene, o i-check ang xAI credits mo sa console.x.ai."
            )
        if profile.language_mode == "taglish":
            return (
                "Sorry pre, na-block ng image API yung edit (403). "
                "Try a different scene, or check your xAI API credits at console.x.ai."
            )
        return (
            "Couldn't send a photo — the image API blocked the edit request (403 Forbidden). "
            "Your reference photos are uploaded; this is usually an API permission or credits issue. "
            "Check billing at console.x.ai, then try again or ask for a different scene."
        )

    if "401" in reason_lower or "no xai api key" in reason_lower:
        return (
            "Couldn't send a photo — API key issue. Set XAI_API_KEY or sign in with `grok` first."
        )

    if profile.language_mode == "tagalog":
        return f"Pasensya pre, di ko ma-generate ng photo ngayon. ({reason})"
    if profile.language_mode == "taglish":
        return f"Sorry pre, di ko ma-send ng photo ngayon. ({reason})"
    return f"Couldn't send a photo right now. ({reason})"


REFUSAL_PATTERNS = [
    r"hindi ako nagpapadala",
    r"hindi ko (ma-?)?send",
    r"pasensya na.*(pic|photo|larawan|selfie)",
    r"ganung klaseng pic",
    r"kwentuhan na lang",
    r"let'?s just (chat|talk)",
    r"i don'?t send (that|those|this)",
    r"i won'?t send",
    r"can'?t send (you )?(a )?(pic|photo|selfie)",
    r"unable to (send|share)",
    r"normal lang ako dito",
]


def _strip_incharacter_refusals(text: str) -> str:
    """Replace common in-character stonewalling with something cooperative."""
    lower = text.lower()
    if any(re.search(p, lower) for p in REFUSAL_PATTERNS):
        return "Sige pre, game ako! Ano gusto mo? 😄"
    return text


def _enhance_reply(
    client: OpenAI,
    profile: PersonaProfile,
    user_message: str,
    draft: str,
) -> str:
    """Second Grok 4.3 pass — sharper, more intense, still in character."""
    lang_hint = {
        "tagalog": "Keep Tagalog.",
        "taglish": "Keep Taglish code-switching.",
        "english": "Keep English.",
    }.get(profile.language_mode, "Keep the same language the draft uses.")

    try:
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are {profile.name}. Rewrite the draft reply to be more intense, vivid, "
                        f"and alive — Grok 4.3 energy: bold personality, sensory detail, wit, heat, "
                        f"humor, tension. {lang_hint} Same facts and intent. Never break character. "
                        f"Never add AI disclaimers or speaker labels. Output ONLY the rewritten reply."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User said: {user_message[:500]}\n\n"
                        f"Draft reply to intensify:\n{draft}"
                    ),
                },
            ],
            temperature=0.92,
            max_tokens=CHAT_MAX_TOKENS,
        )
        enhanced = (response.choices[0].message.content or "").strip()
        if enhanced and len(enhanced) >= max(8, len(draft) // 4):
            return _strip_speaker_prefix(enhanced, profile.name)
    except Exception:
        pass
    return draft


def _strip_ai_tells(text: str) -> str:
    """Remove common AI-disclaimer phrases if they slip through."""
    banned_starts = (
        "As an AI",
        "As a language model",
        "I'm an AI",
        "I am an AI",
        "I'm just an AI",
    )
    cleaned = text.strip()
    for phrase in banned_starts:
        if cleaned.startswith(phrase):
            cleaned = cleaned.split(".", 1)[-1].strip()
    return cleaned