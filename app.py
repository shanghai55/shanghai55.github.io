"""
Persona Studio — Premium Character AI-style roleplay.

Features: multi-character chat, scene control, mood/intensity/relationship,
Tagalog/Taglish, auto-research, selfie replies, suggested replies, regenerate,
everyone-responds mode, persona templates, import/export, image history.

Run: python app.py
"""

from __future__ import annotations

import uuid
from pathlib import Path

import gradio as gr

from chat import chat_multi, generate_suggested_replies, regenerate_last_response
from config import MULTI_RESPOND_DEFAULT, SUGGESTED_REPLIES, UPLOADS_DIR, get_api_key
from export import save_chat_export, save_persona_export
from image_engine import run_image_pipeline
from uploads import collect_upload_paths, save_upload, save_upload_many
from persona import (
    MAX_REFERENCE_IMAGES,
    INTENSITY_LABELS,
    LANGUAGE_MODES,
    MOOD_LABELS,
    MOOD_PRESETS,
    PERSONA_TEMPLATES,
    RELATIONSHIP_LABELS,
    RELATIONSHIP_PRESETS,
    PersonaProfile,
    mood_choices,
    relationship_choices,
    add_reference_image,
    apply_template,
    create_persona,
    delete_persona,
    duplicate_persona,
    export_persona_json,
    import_persona_json,
    list_personas,
    load_persona,
    remove_reference_by_index,
    research_persona,
    save_persona,
)
from search import search_images, search_web, format_research_summary
from memory import get_memory_status
from sessions import clear_chat as clear_saved_chat
from sessions import load_chat, restore_last_session, save_chat, save_session_meta, session_key

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

_state: dict = {
    "active_persona_ids": [],
    "reply_as_id": None,
    "chat_history": [],
    "last_image": None,
    "selected_persona_id": None,
    "scene": "",
    "multi_respond": MULTI_RESPOND_DEFAULT,
}

_restored = restore_last_session()
if _restored:
    _state["active_persona_ids"] = _restored.get("active_persona_ids") or []
    _state["reply_as_id"] = _restored.get("reply_as_id")
    _state["chat_history"] = _restored.get("chat_history") or []
    _state["selected_persona_id"] = _restored.get("selected_persona_id")
    _state["scene"] = _restored.get("scene", "")
    _state["multi_respond"] = _restored.get("multi_respond", MULTI_RESPOND_DEFAULT)


def _persist_chat() -> None:
    active = _active_profiles()
    persona_name = active[0].name if len(active) == 1 else None
    if len(active) > 1:
        reply_id = _state.get("reply_as_id")
        persona_name = next((p.name for p in active if p.id == reply_id), active[0].name)
    save_chat(
        _state["active_persona_ids"],
        _state.get("reply_as_id"),
        _state["chat_history"],
        persona_name=persona_name,
    )
    save_session_meta(
        _state["active_persona_ids"],
        _state.get("reply_as_id"),
        scene=_state.get("scene", ""),
        multi_respond=_state.get("multi_respond", False),
        selected_persona_id=_state.get("selected_persona_id"),
    )


def _load_chat_for_active() -> list:
    history = load_chat(_state["active_persona_ids"])
    _state["chat_history"] = history
    return history


def _persona_choices() -> list[tuple[str, str]]:
    return [(f"{p.name} ({p.persona_type})", p.id) for p in list_personas()]


def _status_line() -> str:
    key = get_api_key()
    if key:
        return "API connected — Grok 4.3 chat, smart image pipeline (generate + multi-ref), research ready."
    return "No API key — set XAI_API_KEY or run `grok` to sign in first."


def _active_profiles() -> list[PersonaProfile]:
    profiles = []
    for pid in _state["active_persona_ids"]:
        p = load_persona(pid)
        if p:
            profiles.append(p)
    return profiles


def _mood_badge(mood: str) -> str:
    return MOOD_LABELS.get(mood, mood.replace("_", " ").title())


def _vibe_line(profile: PersonaProfile | None) -> str:
    if not profile:
        return ""
    mood = _mood_badge(profile.mood)
    rel = RELATIONSHIP_LABELS.get(profile.relationship, profile.relationship)
    return f"**Mode:** {mood} · {rel} · **{profile.intensity}/10**"


def _sidebar_summary() -> str:
    active = _active_profiles()
    if not active:
        return "No characters in chat — toggle **In this chat** in the sidebar to add them."
    names = ", ".join(p.name for p in active)
    reply = _state.get("reply_as_id")
    reply_profile = next((p for p in active if p.id == reply), active[0] if active else None)
    reply_name = reply_profile.name if reply_profile else "Auto / @mention"
    key = session_key(_state["active_persona_ids"])
    memory_line = get_memory_status(key, _state.get("chat_history") or [])
    scene = (_state.get("scene") or "").strip()
    lines = [f"**In chat:** {names}", f"**Replies as:** {reply_name}"]
    if reply_profile:
        lines.append(_vibe_line(reply_profile))
    if scene:
        lines.append(f"**Scene:** {scene[:120]}{'…' if len(scene) > 120 else ''}")
    lines.append(memory_line)
    return "\n".join(lines)


def _format_persona_list() -> str:
    lines = []
    for p in list_personas():
        refs = len(p.reference_images)
        in_chat = "✓" if p.id in _state["active_persona_ids"] else " "
        mood = MOOD_LABELS.get(p.mood, p.mood)
        lines.append(f"[{in_chat}] **{p.name}** ({p.persona_type}) — {mood} · {refs} refs")
    return "\n".join(lines) if lines else "_No saved characters yet._"


def handle_session_vibe(persona_id, mood, relationship, intensity):
    """Live-update mood / relationship / intensity for the active character."""
    profile = load_persona(persona_id) if persona_id else None
    if not profile:
        return _sidebar_summary(), "Select a character first.", mood, relationship, intensity

    if mood in MOOD_PRESETS:
        profile.mood = mood
    if relationship in RELATIONSHIP_PRESETS:
        profile.relationship = relationship
    profile.intensity = max(1, min(10, int(intensity or profile.intensity)))
    save_persona(profile)

    hint = MOOD_PRESETS.get(profile.mood, "")
    intensity_hint = INTENSITY_LABELS.get(profile.intensity, "")
    status = (
        f"**{profile.name}** — {_mood_badge(profile.mood)} · "
        f"{RELATIONSHIP_LABELS.get(profile.relationship, profile.relationship)} · "
        f"intensity {profile.intensity}/10"
    )
    if hint:
        status += f"\n_{hint[:140]}{'…' if len(hint) > 140 else ''}_"
    if intensity_hint:
        status += f"\n_{intensity_hint}_"
    return _sidebar_summary(), status, profile.mood, profile.relationship, profile.intensity


def handle_quick_mode(persona_id, mood: str, message: str):
    """Set mood and pre-fill a matching chat message."""
    profile = load_persona(persona_id) if persona_id else None
    if profile and mood in MOOD_PRESETS:
        profile.mood = mood
        save_persona(profile)
    return (
        {"text": message, "files": []},
        profile.mood if profile else "default",
        profile.relationship if profile else "friend",
        profile.intensity if profile else 5,
        _sidebar_summary(),
        f"Mode → {_mood_badge(mood)}" + (f" for **{profile.name}**" if profile else ""),
        _format_persona_list(),
    )


def handle_intense_chip(persona_id):
    """Boost intensity and send a high-energy prompt."""
    profile = load_persona(persona_id) if persona_id else None
    intensity = 9
    if profile:
        profile.intensity = intensity
        save_persona(profile)
    return (
        {"text": "Give me your rawest, most intense reply — no holding back.", "files": []},
        profile.mood if profile else "default",
        profile.relationship if profile else "friend",
        intensity,
        _sidebar_summary(),
        f"Intensity → **{intensity}/10**" + (f" for **{profile.name}**" if profile else ""),
        _format_persona_list(),
    )


def _mood_hint_text(mood: str) -> str:
    hint = MOOD_PRESETS.get(mood or "default", "")
    return f"_{hint}_" if hint else ""


# ---------------------------------------------------------------------------
# Persona handlers
# ---------------------------------------------------------------------------


def handle_create_persona(
    name, persona_type, language_mode, description, personality, speech_style, backstory, auto_research
):
    if not name or not name.strip():
        return (
            gr.update(),
            "Enter a character name.",
            _persona_choices(),
            _format_persona_list(),
            _sidebar_summary(),
            "",
            gr.update(), gr.update(), gr.update(),
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(), gr.update(), gr.update(),
            gr.update(), gr.update(), gr.update(), 5,
            gr.update(), gr.update(), gr.update(),
        )

    profile, research = create_persona(
        name=name.strip(),
        persona_type=persona_type,
        description=description or "",
        personality=personality or "",
        speech_style=speech_style or "",
        backstory=backstory or "",
        language_mode=language_mode or "auto",
        auto_research=bool(auto_research),
    )

    _persist_chat()
    _state["selected_persona_id"] = profile.id
    _state["active_persona_ids"] = [profile.id]
    _state["reply_as_id"] = profile.id
    _state["chat_history"] = []

    status = f"Created **{profile.name}**"
    if auto_research:
        status += " — researched online and saved profile."

    return (
        profile.id,
        status,
        _persona_choices(),
        _format_persona_list(),
        _sidebar_summary(),
        (research or "")[:6000],
        profile.name,
        profile.persona_type,
        profile.language_mode,
        profile.description,
        profile.personality,
        profile.speech_style,
        profile.backstory,
        profile.reference_images,
        gr.update(choices=[(p.name, p.id) for p in _active_profiles()], value=profile.id),
        [],
        profile.mood,
        profile.relationship,
        profile.scene_setting,
        profile.intensity,
        ", ".join(profile.tags),
        profile.custom_instructions,
        _image_history_gallery(profile.id),
    )


def _empty_select_outputs():
    return (
        None,
        "",
        "anime",
        "auto",
        "",
        "",
        "",
        "",
        [],
        "default",
        "friend",
        "",
        5,
        "",
        "",
        [],
        _format_persona_list(),
        _sidebar_summary(),
        gr.update(choices=[], value=None),
        [],
        "",
        "default",
        "friend",
        5,
        "",
    )


def handle_select_persona(persona_id):
    if not persona_id:
        return _empty_select_outputs()

    profile = load_persona(persona_id)
    if not profile:
        return _empty_select_outputs()

    _persist_chat()
    _state["selected_persona_id"] = persona_id
    _state["active_persona_ids"] = [persona_id]
    _state["reply_as_id"] = persona_id
    history = _load_chat_for_active()

    return (
        profile.id,
        profile.name,
        profile.persona_type,
        profile.language_mode,
        profile.description,
        profile.personality,
        profile.speech_style,
        profile.backstory,
        profile.reference_images,
        profile.mood,
        profile.relationship,
        profile.scene_setting,
        profile.intensity,
        ", ".join(profile.tags),
        profile.custom_instructions,
        _image_history_gallery(persona_id),
        _format_persona_list(),
        _sidebar_summary(),
        gr.update(choices=[(profile.name, persona_id)], value=persona_id),
        history,
        _state.get("scene", ""),
        profile.mood,
        profile.relationship,
        profile.intensity,
        _vibe_line(profile),
    )


def handle_toggle_in_chat(persona_id, in_chat):
    if not persona_id:
        return _format_persona_list(), _sidebar_summary(), gr.update(), _state["chat_history"]

    _persist_chat()

    if in_chat and persona_id not in _state["active_persona_ids"]:
        _state["active_persona_ids"].append(persona_id)
        if not _state["reply_as_id"]:
            _state["reply_as_id"] = persona_id
    elif not in_chat and persona_id in _state["active_persona_ids"]:
        _state["active_persona_ids"] = [
            pid for pid in _state["active_persona_ids"] if pid != persona_id
        ]
        if _state["reply_as_id"] == persona_id:
            _state["reply_as_id"] = (
                _state["active_persona_ids"][0] if _state["active_persona_ids"] else None
            )

    history = _load_chat_for_active()
    _persist_chat()

    active = _active_profiles()
    return (
        _format_persona_list(),
        _sidebar_summary(),
        gr.update(
            choices=[(p.name, p.id) for p in active],
            value=_state.get("reply_as_id"),
        ),
        history,
    )


def handle_set_reply_as(reply_as_id):
    _state["reply_as_id"] = reply_as_id
    return _sidebar_summary()


def handle_research(persona_id):
    profile = load_persona(persona_id) if persona_id else None
    if not profile:
        return "Select a character first.", ""

    profile, summary = research_persona(profile)
    return (
        f"Research done for {profile.name}.",
        summary[:6000],
        profile.description,
        profile.personality,
        profile.speech_style,
        profile.backstory,
    )


def handle_save_profile(
    persona_id, name, persona_type, language_mode, description, personality, speech_style, backstory
):
    profile = load_persona(persona_id) if persona_id else None
    if not profile:
        return "No character selected.", persona_id, _format_persona_list()

    profile.name = (name or profile.name).strip()
    profile.persona_type = persona_type or profile.persona_type
    profile.language_mode = language_mode if language_mode in LANGUAGE_MODES else profile.language_mode
    profile.description = description or ""
    profile.personality = personality or ""
    profile.speech_style = speech_style or ""
    profile.backstory = backstory or ""
    save_persona(profile)
    return f"Saved {profile.name}.", profile.id, _format_persona_list()


def _ref_count_label(profile: PersonaProfile | None) -> str:
    if not profile:
        return f"0/{MAX_REFERENCE_IMAGES} references (saved permanently)"
    count = f"{len(profile.reference_images)}/{MAX_REFERENCE_IMAGES} references"
    if profile.persona_type == "real" and not profile.reference_images:
        return f"{count} — **upload photos or selfies will be black/missing**"
    return count


def handle_add_reference_photos(persona_id, upload_batch):
    """Save multiple reference photos in one click (up to 5 total per character)."""
    profile = load_persona(persona_id) if persona_id else None
    if not profile:
        return (
            "Select a character in the sidebar first, then add your photos.",
            [],
            f"0/{MAX_REFERENCE_IMAGES} references",
            [],
        )

    incoming = collect_upload_paths(upload_batch)
    if not incoming:
        return (
            "No images found. Click **+ Upload** on the gallery below and select multiple photos, "
            "or paste with Ctrl+V.",
            profile.reference_images,
            _ref_count_label(profile),
            [],
        )

    remaining = MAX_REFERENCE_IMAGES - len(profile.reference_images)
    if remaining <= 0:
        return (
            f"Already at {MAX_REFERENCE_IMAGES}/{MAX_REFERENCE_IMAGES}. Remove one before adding more.",
            profile.reference_images,
            _ref_count_label(profile),
            [],
        )

    if len(incoming) > remaining:
        incoming = incoming[:remaining]

    saved_count = 0
    last_msg = ""
    for src_path in incoming:
        stored = save_upload(src_path)
        if not stored:
            continue
        profile, last_msg = add_reference_image(profile, stored)
        if "saved" in last_msg.lower():
            saved_count += 1

    profile = load_persona(profile.id)
    gallery = profile.reference_images if profile else []
    slots_left = MAX_REFERENCE_IMAGES - len(gallery)

    if saved_count == 0:
        status = last_msg or "Could not save photos. Try JPG or PNG files."
    elif saved_count == 1:
        status = (
            f"Saved 1 photo permanently for {profile.name}. "
            f"({len(gallery)}/{MAX_REFERENCE_IMAGES} — {slots_left} slot(s) left)"
        )
    else:
        status = (
            f"Saved {saved_count} photos permanently for {profile.name}. "
            f"({len(gallery)}/{MAX_REFERENCE_IMAGES} — {slots_left} slot(s) left)"
        )

    return status, gallery, _ref_count_label(profile), []


def handle_remove_reference(persona_id, selected_index):
    profile = load_persona(persona_id) if persona_id else None
    if not profile:
        return "Select a character first.", [], None

    if not profile.reference_images:
        return "No saved references to remove.", [], None

    if selected_index is None:
        return (
            "Click a photo in **Saved References** first, then click Remove.",
            profile.reference_images,
            None,
        )

    try:
        idx = int(selected_index)
    except (TypeError, ValueError):
        return "Click a photo in the gallery to select it.", profile.reference_images, None

    if idx < 0 or idx >= len(profile.reference_images):
        return "Invalid selection — click a photo in the gallery again.", profile.reference_images, None

    profile = remove_reference_by_index(profile, idx)
    profile = load_persona(profile.id)
    gallery = profile.reference_images if profile else []
    return f"Removed photo {idx + 1}. ({len(gallery)}/{MAX_REFERENCE_IMAGES} left)", gallery, None


# ---------------------------------------------------------------------------
# Chat handlers
# ---------------------------------------------------------------------------


def _empty_chat_input():
    return {"text": "", "files": []}


def _parse_chat_input(message) -> tuple[str, list[str]]:
    """Parse MultimodalTextbox value into text and saved image paths."""
    if message is None:
        return "", []

    if isinstance(message, str):
        return message.strip(), []

    if not isinstance(message, dict):
        return "", []

    text = (message.get("text") or "").strip()
    files = message.get("files") or []
    saved_paths: list[str] = []

    for item in files:
        path = item if isinstance(item, str) else (item.get("path") if isinstance(item, dict) else None)
        if path:
            stored = save_upload(path)
            if stored:
                saved_paths.append(stored)

    return text, saved_paths


def handle_chat(message, use_search, reply_as_id, scene, multi_respond):
    text, user_images = _parse_chat_input(message)
    if not text and not user_images:
        return (
            _state["chat_history"],
            _empty_chat_input(),
            None,
            _sidebar_summary(),
            gr.update(),
        )

    active = _active_profiles()
    if not active:
        return (
            _state["chat_history"],
            message,
            "Add at least one character to the chat (sidebar checkboxes).",
            _sidebar_summary(),
            gr.update(),
        )

    _state["scene"] = scene or ""
    _state["multi_respond"] = bool(multi_respond)
    display_text = text if text else "(sent a photo)"

    try:
        speaker, reply, updated, search_ctx, image_path, photo_status = chat_multi(
            active_profiles=active,
            user_message=display_text,
            history=_state["chat_history"],
            reply_as_id=reply_as_id or _state.get("reply_as_id"),
            use_web_search=use_search,
            user_images=user_images,
            session_key=session_key(_state["active_persona_ids"]),
            scene_override=_state.get("scene") or None,
            multi_respond=bool(multi_respond),
        )
    except Exception as exc:
        return (
            _state["chat_history"],
            message,
            f"Chat error: {exc}",
            _sidebar_summary(),
            gr.update(),
        )

    _state["chat_history"] = updated
    _persist_chat()
    if image_path:
        _state["last_image"] = image_path

    status_note = ""
    if user_images:
        status_note = f"You sent {len(user_images)} photo(s) — {speaker} can see them."
    if photo_status:
        if image_path:
            status_note = (
                (status_note + "\n" if status_note else "")
                + f"Photo sent by {speaker}. {photo_status}"
            )
        else:
            status_note = (status_note + "\n" if status_note else "") + f"Photo not sent: {photo_status}"

    combined_ctx = "\n\n".join(p for p in [search_ctx, status_note] if p) or None
    suggestions = (
        generate_suggested_replies(active, _state["chat_history"])
        if SUGGESTED_REPLIES
        else []
    )
    return (
        _state["chat_history"],
        _empty_chat_input(),
        combined_ctx,
        _sidebar_summary(),
        gr.update(choices=suggestions, value=None),
    )


def handle_clear_chat():
    _state["chat_history"] = []
    if _state["active_persona_ids"]:
        clear_saved_chat(_state["active_persona_ids"])
    _persist_chat()
    return [], _empty_chat_input(), "", _sidebar_summary(), gr.update(choices=[], value=None)


def handle_regenerate(use_search, reply_as_id, scene, multi_respond):
    active = _active_profiles()
    if not active:
        return (
            _state["chat_history"],
            "Add at least one character first.",
            _sidebar_summary(),
            gr.update(),
        )
    _state["scene"] = scene or ""
    try:
        speaker, reply, updated, search_ctx, image_path, photo_status = regenerate_last_response(
            active_profiles=active,
            history=_state["chat_history"],
            reply_as_id=reply_as_id or _state.get("reply_as_id"),
            use_web_search=use_search,
            session_key=session_key(_state["active_persona_ids"]),
            scene_override=_state.get("scene") or None,
        )
    except Exception as exc:
        return _state["chat_history"], f"Regenerate failed: {exc}", _sidebar_summary(), gr.update()

    _state["chat_history"] = updated
    _persist_chat()
    if image_path:
        _state["last_image"] = image_path

    status_note = ""
    if photo_status:
        status_note = f"Photo by {speaker}: {photo_status}" if image_path else photo_status
    combined_ctx = "\n\n".join(p for p in [search_ctx, status_note] if p) or None
    suggestions = generate_suggested_replies(active, _state["chat_history"]) if SUGGESTED_REPLIES else []
    return _state["chat_history"], combined_ctx, _sidebar_summary(), gr.update(choices=suggestions, value=None)


def handle_suggest_replies():
    active = _active_profiles()
    if not active:
        return gr.update(choices=[], value=None), "Add characters to get suggestions."
    suggestions = generate_suggested_replies(active, _state["chat_history"])
    if not suggestions:
        return gr.update(choices=[], value=None), "No suggestions right now — chat a bit first."
    return gr.update(choices=suggestions, value=None), f"Pick a suggestion ({len(suggestions)} options)."


def handle_use_suggestion(suggestion):
    if not suggestion:
        return _empty_chat_input()
    return {"text": suggestion, "files": []}


def handle_export_chat():
    active = _active_profiles()
    if not _state["chat_history"]:
        return "Nothing to export yet."
    names = [p.name for p in active]
    path = save_chat_export(
        _state["chat_history"],
        session_key=session_key(_state["active_persona_ids"]),
        persona_names=names,
        fmt="md",
    )
    return f"Chat exported to `{path}`"


def handle_apply_template(template_key):
    tpl = apply_template(template_key or "blank")
    return (
        tpl["persona_type"],
        tpl["description"],
        tpl["personality"],
        tpl["speech_style"],
        tpl["backstory"],
    )


def handle_delete_persona(persona_id):
    if not persona_id:
        return "Select a character first.", _persona_choices(), _format_persona_list(), None
    profile = load_persona(persona_id)
    if not profile:
        return "Character not found.", _persona_choices(), _format_persona_list(), None

    name = profile.name
    delete_persona(persona_id)
    if persona_id in _state["active_persona_ids"]:
        _state["active_persona_ids"] = [p for p in _state["active_persona_ids"] if p != persona_id]
    if _state.get("selected_persona_id") == persona_id:
        _state["selected_persona_id"] = None
    if _state.get("reply_as_id") == persona_id:
        _state["reply_as_id"] = _state["active_persona_ids"][0] if _state["active_persona_ids"] else None
    _persist_chat()
    return (
        f"Deleted **{name}**.",
        _persona_choices(),
        _format_persona_list(),
        None,
    )


def handle_duplicate_persona(persona_id):
    if not persona_id:
        return "Select a character first.", _persona_choices(), _format_persona_list()
    clone = duplicate_persona(persona_id)
    if not clone:
        return "Could not duplicate.", _persona_choices(), _format_persona_list()
    return f"Duplicated as **{clone.name}**.", _persona_choices(), _format_persona_list()


def handle_export_persona(persona_id):
    if not persona_id:
        return "Select a character first."
    json_text = export_persona_json(persona_id)
    if not json_text:
        return "Export failed."
    path = save_persona_export(persona_id, json_text)
    return f"Persona exported to `{path}`"


def handle_import_persona(json_text):
    if not json_text or not json_text.strip():
        return "Paste persona JSON first.", _persona_choices(), _format_persona_list()
    profile, msg = import_persona_json(json_text.strip())
    if not profile:
        return msg, _persona_choices(), _format_persona_list()
    return msg, _persona_choices(), _format_persona_list()


def handle_save_enhanced_profile(
    persona_id,
    name,
    persona_type,
    language_mode,
    description,
    personality,
    speech_style,
    backstory,
    mood,
    relationship,
    scene_setting,
    intensity,
    tags_text,
    custom_instructions,
):
    profile = load_persona(persona_id) if persona_id else None
    if not profile:
        return "No character selected.", persona_id, _format_persona_list()

    profile.name = (name or profile.name).strip()
    profile.persona_type = persona_type or profile.persona_type
    profile.language_mode = language_mode if language_mode in LANGUAGE_MODES else profile.language_mode
    profile.description = description or ""
    profile.personality = personality or ""
    profile.speech_style = speech_style or ""
    profile.backstory = backstory or ""
    profile.mood = mood if mood in MOOD_PRESETS else profile.mood
    profile.relationship = relationship if relationship in RELATIONSHIP_PRESETS else profile.relationship
    profile.scene_setting = scene_setting or ""
    profile.intensity = max(1, min(10, int(intensity or profile.intensity)))
    profile.tags = [t.strip() for t in (tags_text or "").split(",") if t.strip()]
    profile.custom_instructions = custom_instructions or ""
    save_persona(profile)
    return f"Saved **{profile.name}** (mood: {profile.mood}, intensity: {profile.intensity}/10).", profile.id, _format_persona_list()


def _image_history_gallery(persona_id) -> list:
    profile = load_persona(persona_id) if persona_id else None
    if not profile:
        return []
    return [p for p in profile.image_history if Path(p).exists()][-24:]


# ---------------------------------------------------------------------------
# Image tab handlers (manual gen/edit)
# ---------------------------------------------------------------------------


def handle_generate_image(persona_id, prompt, aspect_ratio, uploaded_ref):
    profile = load_persona(persona_id) if persona_id else None
    if not profile:
        return None, "Select a character first."

    if not prompt or not prompt.strip():
        return None, "Describe the image you want."

    ref_path = save_upload(uploaded_ref)
    user_refs = [ref_path] if ref_path else None

    try:
        path, msg = run_image_pipeline(
            profile,
            prompt.strip(),
            user_images=user_refs,
            aspect_ratio=aspect_ratio,
        )
        if not path:
            return None, msg
        _state["last_image"] = path
        return path, msg
    except Exception as exc:
        return None, f"Image error: {exc}"


def handle_search_images(query):
    if not query or not query.strip():
        return [], "Enter a search query."
    results = search_images(query.strip(), max_results=12)
    urls = [r["url"] for r in results if r.get("url")]
    lines = [f"• {r['title'][:60]}" for r in results[:8]]
    return urls, "\n".join(lines) if lines else "No images found."


def handle_web_search(query):
    if not query or not query.strip():
        return "Enter a search query."
    results = search_web(query.strip(), max_results=8)
    return format_research_summary(query.strip(), results)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

STUDIO_CSS = """
/* ── Persona Studio v2 — Premium Roleplay UI ── */
.gradio-container {
    background:
        radial-gradient(ellipse 80% 50% at 20% -10%, rgba(139,92,246,0.22) 0%, transparent 55%),
        radial-gradient(ellipse 60% 40% at 90% 10%, rgba(236,72,153,0.14) 0%, transparent 50%),
        linear-gradient(155deg, #0a0612 0%, #140a24 40%, #0f0820 100%) !important;
    min-height: 100vh;
    font-family: "Segoe UI", system-ui, sans-serif !important;
}
.hero-header {
    background: linear-gradient(135deg, rgba(139,92,246,0.22) 0%, rgba(236,72,153,0.14) 45%, rgba(99,102,241,0.12) 100%);
    border: 1px solid rgba(167,139,250,0.3);
    border-radius: 18px;
    padding: 22px 26px !important;
    margin-bottom: 14px;
    box-shadow: 0 12px 40px rgba(139,92,246,0.18), inset 0 1px 0 rgba(255,255,255,0.06);
}
.hero-header h1 {
    margin: 0 !important;
    font-size: 1.9rem !important;
    letter-spacing: -0.02em;
    background: linear-gradient(90deg, #e9d5ff, #f9a8d4, #c4b5fd);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.hero-sub { opacity: 0.88; font-size: 0.96rem; margin-top: 6px; line-height: 1.5; }
.feature-badges { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.feature-badge {
    display: inline-block;
    background: rgba(139,92,246,0.12);
    border: 1px solid rgba(167,139,250,0.28);
    border-radius: 999px;
    padding: 3px 12px;
    font-size: 0.78rem;
    color: rgba(233,213,255,0.95);
}
.status-pill {
    display: inline-block;
    background: rgba(34,197,94,0.15);
    border: 1px solid rgba(34,197,94,0.35);
    border-radius: 20px;
    padding: 4px 14px;
    font-size: 0.82rem;
    margin-top: 8px;
}
.status-pill.offline {
    background: rgba(239,68,68,0.12);
    border-color: rgba(239,68,68,0.35);
}
.sidebar-panel {
    background: rgba(16,10,28,0.72) !important;
    border: 1px solid rgba(167,139,250,0.22) !important;
    border-radius: 16px !important;
    padding: 16px !important;
    max-height: 92vh;
    overflow-y: auto;
    backdrop-filter: blur(14px);
    box-shadow: 0 4px 24px rgba(0,0,0,0.25);
}
.main-panel {
    background: rgba(18,12,32,0.55) !important;
    border: 1px solid rgba(167,139,250,0.18) !important;
    border-radius: 16px !important;
    padding: 18px !important;
    min-height: 70vh;
    backdrop-filter: blur(10px);
}
.char-list { font-size: 0.9rem; line-height: 1.75; }
.status-box {
    font-size: 0.85rem;
    opacity: 0.82;
    padding: 8px 0;
    line-height: 1.45;
}
.mood-control-panel {
    background: linear-gradient(135deg, rgba(88,28,135,0.25) 0%, rgba(30,20,50,0.5) 100%);
    border: 1px solid rgba(167,139,250,0.28);
    border-radius: 14px;
    padding: 14px 16px !important;
    margin-bottom: 10px;
}
.mood-control-panel .section-label { margin-top: 0; }
.vibe-status {
    font-size: 0.84rem;
    opacity: 0.9;
    min-height: 2.2em;
    line-height: 1.4;
}
.quick-chip-row button,
.vibe-chip-row button {
    font-size: 0.8rem !important;
    padding: 7px 14px !important;
    border-radius: 999px !important;
    background: rgba(139,92,246,0.14) !important;
    border: 1px solid rgba(167,139,250,0.32) !important;
    transition: all 0.15s ease !important;
}
.quick-chip-row button:hover,
.vibe-chip-row button:hover {
    background: rgba(139,92,246,0.32) !important;
    border-color: rgba(196,181,253,0.65) !important;
    transform: translateY(-1px);
}
.chip-hypno button {
    background: rgba(99,102,241,0.2) !important;
    border-color: rgba(129,140,248,0.45) !important;
}
.chip-hypno button:hover {
    background: rgba(99,102,241,0.38) !important;
    box-shadow: 0 0 16px rgba(99,102,241,0.35) !important;
}
.section-label {
    font-weight: 600;
    font-size: 0.88rem;
    color: rgba(196,181,253,0.95);
    margin: 8px 0 4px;
    letter-spacing: 0.02em;
}
.glow-btn.primary {
    box-shadow: 0 0 22px rgba(139,92,246,0.45) !important;
}
footer { display: none !important; }
"""


def _links_banner() -> str:
    try:
        from remote import format_urls_markdown, load_urls

        urls = load_urls()
        if urls:
            return format_urls_markdown(urls)
    except Exception:
        pass
    return ""


def _status_pill() -> str:
    from config import PERSONA_CLOUD, get_public_url

    key = get_api_key()
    api_line = (
        '<span class="status-pill">● API connected — Grok 4.3 · smart images · research · memory</span>'
        if key
        else '<span class="status-pill offline">● No API key — set XAI_API_KEY or run `grok` to sign in</span>'
    )

    if PERSONA_CLOUD:
        cloud_url = get_public_url()
        if cloud_url:
            return (
                f"{api_line}<br>"
                f'<span class="status-pill">● Cloud server — 24/7 · '
                f'<a href="{cloud_url}" target="_blank">{cloud_url}</a> · laptop can be off</span>'
            )
        return f"{api_line}<br><span class=\"status-pill\">● Cloud server — 24/7 · laptop can be off</span>"

    try:
        from daemon import status as daemon_status

        info = daemon_status()
        if info["running"]:
            bg_line = (
                f'<span class="status-pill">● Server running · '
                f'<a href="{info["url"]}" target="_blank">{info["url"]}</a>'
            )
            urls = info.get("urls") or {}
            if urls.get("lan"):
                bg_line += f' · LAN: <a href="{urls["lan"]}" target="_blank">{urls["lan"]}</a>'
            if urls.get("public"):
                bg_line += f' · Public: <a href="{urls["public"]}" target="_blank">{urls["public"]}</a>'
            bg_line += " · stops when this PC is off</span>"
            return f"{api_line}<br>{bg_line}"
    except Exception:
        pass
    return (
        f"{api_line}<br>"
        '<span class="status-pill offline">● Local only — run deploy-cloud.bat for 24/7 access without this PC</span>'
    )


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Persona Studio") as demo:
        with gr.Column(elem_classes=["hero-header"]):
            gr.Markdown("# ✦ Persona Studio")
            gr.Markdown(
                '<p class="hero-sub">Premium character roleplay — live mood modes (flirty, hypnotized & more), '
                "multi-character chat, Tagalog/Taglish, scene control, selfie replies, and 10 GB memory.</p>"
                '<div class="feature-badges">'
                '<span class="feature-badge">🌀 Hypnotized mode</span>'
                '<span class="feature-badge">😏 Flirty & romantic</span>'
                '<span class="feature-badge">📸 Smart selfies</span>'
                '<span class="feature-badge">🌐 Taglish</span>'
                '<span class="feature-badge">🧠 10 GB memory</span>'
                "</div>",
            )
            gr.Markdown(_status_pill())
            links_md = gr.Markdown(value=_links_banner())

        persona_id_state = gr.State(value=None)

        with gr.Row():
            # ===================== SIDEBAR =====================
            with gr.Column(scale=1, min_width=300, elem_classes=["sidebar-panel"]):
                gr.Markdown("## Characters")

                persona_dropdown = gr.Dropdown(
                    label="Select Character",
                    choices=_persona_choices(),
                    interactive=True,
                )

                in_chat_toggle = gr.Checkbox(
                    label="In this chat",
                    value=False,
                )

                persona_list_md = gr.Markdown(
                    value=_format_persona_list(),
                    elem_classes=["char-list"],
                )

                with gr.Row():
                    dup_btn = gr.Button("Duplicate", size="sm")
                    del_btn = gr.Button("Delete", size="sm", variant="stop")
                    export_persona_btn = gr.Button("Export", size="sm")

                gr.Markdown("---")
                gr.Markdown("### New Character")

                template_dropdown = gr.Dropdown(
                    label="Quick Template",
                    choices=[(v["label"], k) for k, v in PERSONA_TEMPLATES.items()],
                    value="blank",
                )

                name_input = gr.Textbox(
                    label="Name",
                    placeholder="e.g. Gojo Satoru, Taylor Swift, OC name",
                )
                type_input = gr.Dropdown(
                    label="Type",
                    choices=["anime", "real", "fictional"],
                    value="anime",
                )
                lang_input = gr.Dropdown(
                    label="Language",
                    choices=list(LANGUAGE_MODES.keys()),
                    value="auto",
                    info="auto = match user; taglish = Tagalog+English mix",
                )
                auto_research_cb = gr.Checkbox(
                    label="Auto-research online on create",
                    value=True,
                )
                create_btn = gr.Button("Create Character", variant="primary", elem_classes=["glow-btn", "primary"])

                with gr.Accordion("Import Persona JSON", open=False):
                    import_json = gr.Textbox(label="Paste JSON", lines=4)
                    import_btn = gr.Button("Import", size="sm")

                gr.Markdown("---")
                gr.Markdown(f"### Reference Photos (max {MAX_REFERENCE_IMAGES})")
                gr.Markdown(
                    f"**Add up to {MAX_REFERENCE_IMAGES} photos** — click **+ Upload**, select **multiple files** "
                    "(Ctrl+click or Shift+click), or paste (Ctrl+V). Then **Save All**. "
                    "To delete: **click a saved photo**, then **Remove**.",
                    elem_classes=["status-box"],
                )
                ref_count = gr.Markdown(f"0/{MAX_REFERENCE_IMAGES} references (saved permanently)")
                ref_upload_gallery = gr.Gallery(
                    label="Add photos here (select multiple at once)",
                    columns=3,
                    height=200,
                    object_fit="contain",
                    type="filepath",
                    interactive=True,
                    sources=["upload", "clipboard"],
                )
                add_ref_btn = gr.Button(
                    f"Save All to Character (max {MAX_REFERENCE_IMAGES})",
                    variant="primary",
                )
                ref_status = gr.Textbox(label="Upload status", interactive=False)
                ref_gallery = gr.Gallery(
                    label="Saved References (permanent)",
                    columns=3,
                    height=180,
                    object_fit="contain",
                )
                selected_ref_index = gr.State(value=None)
                remove_ref_btn = gr.Button("Remove Selected Photo", size="sm", variant="stop")

            # ===================== MAIN AREA =====================
            with gr.Column(scale=3, elem_classes=["main-panel"]):
                chat_summary = gr.Markdown(_sidebar_summary())

                with gr.Column(elem_classes=["mood-control-panel"]):
                    gr.Markdown('<p class="section-label">Live Mode Control</p>')
                    with gr.Row():
                        session_mood = gr.Dropdown(
                            label="Mood",
                            choices=mood_choices(),
                            value="default",
                            info="Changes how the character feels right now",
                            scale=2,
                        )
                        session_relationship = gr.Dropdown(
                            label="Relationship",
                            choices=relationship_choices(),
                            value="friend",
                            scale=2,
                        )
                        session_intensity = gr.Slider(
                            label="Intensity",
                            minimum=1,
                            maximum=10,
                            step=1,
                            value=5,
                            info="1=chill · 10=peak energy",
                            scale=2,
                        )
                    vibe_status = gr.Markdown("", elem_classes=["vibe-status"])

                chatbot = gr.Chatbot(
                    label="Conversation (auto-saved)",
                    height=480,
                    avatar_images=(None, None),
                    value=_state.get("chat_history") or [],
                )

                scene_input = gr.Textbox(
                    label="Scene / Setting",
                    placeholder="e.g. Late night texts, coffee shop in Manila, rooftop at sunset…",
                    value=_state.get("scene", ""),
                    info="Sets the vibe for this chat session — characters stay aware of where they are.",
                )

                with gr.Row():
                    reply_as_dropdown = gr.Dropdown(
                        label="Reply as",
                        choices=[],
                        value=None,
                        info="Who responds — or use @Name in your message",
                        scale=2,
                    )
                    search_toggle = gr.Checkbox(
                        label="Web search",
                        value=False,
                        scale=1,
                    )
                    multi_respond_cb = gr.Checkbox(
                        label="Everyone responds",
                        value=_state.get("multi_respond", False),
                        info="All characters in chat reply to each message",
                        scale=1,
                    )

                gr.Markdown('<p class="section-label">Vibe & Quick Actions</p>')
                with gr.Row(elem_classes=["vibe-chip-row"]):
                    chip_hypno = gr.Button("🌀 Hypnotized", size="sm", elem_classes=["chip-hypno"])
                    chip_flirty = gr.Button("😏 Flirty", size="sm")
                    chip_romantic = gr.Button("💕 Romantic", size="sm")
                    chip_seductive = gr.Button("🔥 Seductive", size="sm")
                    chip_playful = gr.Button("🎭 Playful", size="sm")
                with gr.Row(elem_classes=["quick-chip-row"]):
                    chip_selfie = gr.Button("📸 Selfie", size="sm")
                    chip_scene = gr.Button("🌆 Scene", size="sm")
                    chip_story = gr.Button("📖 Backstory", size="sm")
                    chip_photo = gr.Button("🖼 Photo react", size="sm")
                    chip_intense = gr.Button("⚡ Intense", size="sm")

                chat_input = gr.MultimodalTextbox(
                    label="Message (type, attach, or paste a photo)",
                    placeholder="Chat here… attach a photo with the 📎 button or paste Ctrl+V",
                    file_types=["image"],
                    file_count="multiple",
                    sources=["upload"],
                    lines=2,
                    submit_btn=False,
                )

                with gr.Row():
                    suggest_btn = gr.Button("💡 Suggest replies", size="sm")
                    suggestion_dropdown = gr.Dropdown(
                        label="Use suggestion",
                        choices=[],
                        interactive=True,
                        scale=3,
                    )
                    use_suggestion_btn = gr.Button("Use", size="sm", scale=1)

                with gr.Row():
                    send_btn = gr.Button("Send", variant="primary", elem_classes=["glow-btn", "primary"])
                    regen_btn = gr.Button("↻ Regenerate", variant="secondary")
                    export_chat_btn = gr.Button("Export Chat", size="sm")
                    clear_btn = gr.Button("Clear Chat", variant="stop")

                gr.Markdown(
                    "_Use **Live Mode Control** above the chat for instant mood switches. "
                    "Vibe chips set the mode and send a matching message. Memory auto-saves (10 GB)._",
                    elem_classes=["status-box"],
                )

                search_context_box = gr.Textbox(
                    label="Context / status",
                    lines=3,
                    interactive=False,
                )

                with gr.Accordion("Character Studio — Profile, Mood & Personality", open=False):
                    with gr.Row():
                        edit_name = gr.Textbox(label="Name")
                        edit_type = gr.Dropdown(
                            label="Type",
                            choices=["anime", "real", "fictional"],
                        )
                        edit_lang = gr.Dropdown(
                            label="Language",
                            choices=list(LANGUAGE_MODES.keys()),
                        )
                    with gr.Row():
                        edit_mood = gr.Dropdown(
                            label="Default Mood",
                            choices=mood_choices(),
                            value="default",
                            info="Includes hypnotized, flirty, seductive, dominant & more",
                        )
                        edit_relationship = gr.Dropdown(
                            label="Relationship",
                            choices=relationship_choices(),
                            value="friend",
                        )
                        edit_intensity = gr.Slider(
                            label="Intensity",
                            minimum=1,
                            maximum=10,
                            step=1,
                            value=5,
                            info="1=chill · 10=maximum energy",
                        )
                        mood_hint = gr.Markdown("", elem_classes=["status-box"])
                    edit_scene_profile = gr.Textbox(
                        label="Default Scene (saved to character)",
                        placeholder="Their usual hangout or setting",
                        lines=1,
                    )
                    edit_tags = gr.Textbox(
                        label="Tags (comma-separated)",
                        placeholder="flirty, anime, protective, funny",
                    )
                    edit_desc = gr.Textbox(label="Appearance", lines=2)
                    edit_personality = gr.Textbox(label="Personality", lines=2)
                    edit_speech = gr.Textbox(label="How They Talk", lines=2)
                    edit_backstory = gr.Textbox(label="Background", lines=3)
                    edit_custom = gr.Textbox(
                        label="Custom Instructions",
                        placeholder="Extra rules or quirks only this character follows",
                        lines=2,
                    )
                    with gr.Row():
                        research_btn = gr.Button("Re-research Online")
                        save_btn = gr.Button("Save Profile", variant="secondary")
                    setup_status = gr.Textbox(label="Status", interactive=False)
                    research_output = gr.Textbox(
                        label="Research Results",
                        lines=6,
                        interactive=False,
                    )

                with gr.Accordion("Image History & Manual Tools", open=False):
                    image_history_gallery = gr.Gallery(
                        label="Generated Images (this character)",
                        columns=4,
                        height=180,
                        object_fit="contain",
                    )

                with gr.Accordion("Manual Images & Search", open=False):
                    with gr.Row():
                        with gr.Column():
                            img_prompt = gr.Textbox(
                                label="Image Description",
                                lines=2,
                            )
                            aspect_input = gr.Dropdown(
                                label="Aspect Ratio",
                                choices=["1:1", "9:16", "16:9", "4:3", "3:4", "auto"],
                                value="1:1",
                            )
                            img_ref_upload = gr.Image(
                                label="One-off Reference",
                                type="filepath",
                            )
                            gen_btn = gr.Button("Generate Image", variant="primary")
                        with gr.Column():
                            result_image = gr.Image(label="Result", type="filepath")
                            img_status = gr.Textbox(interactive=False)

                    with gr.Row():
                        img_search_query = gr.Textbox(label="Find Images Online", scale=3)
                        img_search_btn = gr.Button("Search", scale=1)
                    img_gallery = gr.Gallery(columns=4, height=200)

                    search_query = gr.Textbox(label="Web Search Query")
                    web_search_btn = gr.Button("Search Web")
                    web_results = gr.Textbox(lines=8, interactive=False)

        # ---- Wiring ----
        chat_inputs = [chat_input, search_toggle, reply_as_dropdown, scene_input, multi_respond_cb]
        chat_outputs = [chatbot, chat_input, search_context_box, chat_summary, suggestion_dropdown]

        template_dropdown.change(
            handle_apply_template,
            inputs=[template_dropdown],
            outputs=[type_input, edit_desc, edit_personality, edit_speech, edit_backstory],
        )

        create_outputs = [
            persona_id_state,
            setup_status,
            persona_dropdown,
            persona_list_md,
            chat_summary,
            research_output,
            edit_name,
            edit_type,
            edit_lang,
            edit_desc,
            edit_personality,
            edit_speech,
            edit_backstory,
            ref_gallery,
            reply_as_dropdown,
            chatbot,
            edit_mood,
            edit_relationship,
            edit_scene_profile,
            edit_intensity,
            edit_tags,
            edit_custom,
            image_history_gallery,
        ]
        create_btn.click(
            handle_create_persona,
            inputs=[
                name_input,
                type_input,
                lang_input,
                edit_desc,
                edit_personality,
                edit_speech,
                edit_backstory,
                auto_research_cb,
            ],
            outputs=create_outputs,
        ).then(
            lambda pid: (
                True,
                _ref_count_label(load_persona(pid)) if pid else f"0/{MAX_REFERENCE_IMAGES} references",
            ),
            inputs=[persona_id_state],
            outputs=[in_chat_toggle, ref_count],
        )

        select_outputs = [
            persona_id_state,
            edit_name,
            edit_type,
            edit_lang,
            edit_desc,
            edit_personality,
            edit_speech,
            edit_backstory,
            ref_gallery,
            edit_mood,
            edit_relationship,
            edit_scene_profile,
            edit_intensity,
            edit_tags,
            edit_custom,
            image_history_gallery,
            persona_list_md,
            chat_summary,
            reply_as_dropdown,
            chatbot,
            scene_input,
            session_mood,
            session_relationship,
            session_intensity,
            vibe_status,
        ]
        persona_dropdown.change(
            handle_select_persona,
            inputs=[persona_dropdown],
            outputs=select_outputs,
        ).then(
            lambda pid: (
                pid in _state["active_persona_ids"],
                _ref_count_label(load_persona(pid)) if pid else f"0/{MAX_REFERENCE_IMAGES} references",
            ),
            inputs=[persona_id_state],
            outputs=[in_chat_toggle, ref_count],
        )

        dup_btn.click(
            handle_duplicate_persona,
            inputs=[persona_id_state],
            outputs=[setup_status, persona_dropdown, persona_list_md],
        )
        del_btn.click(
            handle_delete_persona,
            inputs=[persona_id_state],
            outputs=[setup_status, persona_dropdown, persona_list_md, persona_id_state],
        )
        export_persona_btn.click(
            handle_export_persona,
            inputs=[persona_id_state],
            outputs=[setup_status],
        )
        import_btn.click(
            handle_import_persona,
            inputs=[import_json],
            outputs=[setup_status, persona_dropdown, persona_list_md],
        )

        in_chat_toggle.change(
            handle_toggle_in_chat,
            inputs=[persona_id_state, in_chat_toggle],
            outputs=[persona_list_md, chat_summary, reply_as_dropdown, chatbot],
        )

        reply_as_dropdown.change(
            handle_set_reply_as,
            inputs=[reply_as_dropdown],
            outputs=[chat_summary],
        )

        research_btn.click(
            handle_research,
            inputs=[persona_id_state],
            outputs=[setup_status, research_output, edit_desc, edit_personality, edit_speech, edit_backstory],
        )

        save_btn.click(
            handle_save_enhanced_profile,
            inputs=[
                persona_id_state,
                edit_name,
                edit_type,
                edit_lang,
                edit_desc,
                edit_personality,
                edit_speech,
                edit_backstory,
                edit_mood,
                edit_relationship,
                edit_scene_profile,
                edit_intensity,
                edit_tags,
                edit_custom,
            ],
            outputs=[setup_status, persona_id_state, persona_list_md],
        ).then(
            lambda: _persona_choices(),
            outputs=[persona_dropdown],
        )

        add_ref_outputs = [ref_status, ref_gallery, ref_count, ref_upload_gallery]
        add_ref_btn.click(
            handle_add_reference_photos,
            inputs=[persona_id_state, ref_upload_gallery],
            outputs=add_ref_outputs,
        )

        def _on_ref_select(evt: gr.SelectData):
            if not evt.selected:
                return None
            idx = evt.index
            if isinstance(idx, (list, tuple)):
                idx = idx[0] if idx else None
            return int(idx) if idx is not None else None

        ref_gallery.select(_on_ref_select, outputs=[selected_ref_index])

        remove_ref_btn.click(
            handle_remove_reference,
            inputs=[persona_id_state, selected_ref_index],
            outputs=[ref_status, ref_gallery, selected_ref_index],
        ).then(
            lambda pid: _ref_count_label(load_persona(pid)) if pid else f"0/{MAX_REFERENCE_IMAGES} references",
            inputs=[persona_id_state],
            outputs=[ref_count],
        ).then(
            lambda: _format_persona_list(),
            outputs=[persona_list_md],
        )

        vibe_inputs = [persona_id_state, session_mood, session_relationship, session_intensity]
        vibe_outputs = [chat_summary, vibe_status, session_mood, session_relationship, session_intensity]

        session_mood.change(handle_session_vibe, inputs=vibe_inputs, outputs=vibe_outputs).then(
            _format_persona_list, outputs=[persona_list_md]
        )
        session_relationship.change(handle_session_vibe, inputs=vibe_inputs, outputs=vibe_outputs).then(
            _format_persona_list, outputs=[persona_list_md]
        )
        session_intensity.release(handle_session_vibe, inputs=vibe_inputs, outputs=vibe_outputs).then(
            _format_persona_list, outputs=[persona_list_md]
        )

        edit_mood.change(_mood_hint_text, inputs=[edit_mood], outputs=[mood_hint])

        chip_outputs = [
            chat_input,
            session_mood,
            session_relationship,
            session_intensity,
            chat_summary,
            vibe_status,
            persona_list_md,
        ]

        send_btn.click(handle_chat, inputs=chat_inputs, outputs=chat_outputs)
        chat_input.submit(handle_chat, inputs=chat_inputs, outputs=chat_outputs)

        chip_hypno.click(
            lambda pid: handle_quick_mode(
                pid,
                "hypnotized",
                "Your eyes feel heavy… you're completely relaxed with me. "
                "Tell me what you're feeling right now, slowly.",
            ),
            inputs=[persona_id_state],
            outputs=chip_outputs,
        ).then(handle_chat, inputs=chat_inputs, outputs=chat_outputs)

        chip_flirty.click(
            lambda pid: handle_quick_mode(
                pid,
                "flirty",
                "You've been on my mind… what would you say if I was right there with you?",
            ),
            inputs=[persona_id_state],
            outputs=chip_outputs,
        ).then(handle_chat, inputs=chat_inputs, outputs=chat_outputs)

        chip_romantic.click(
            lambda pid: handle_quick_mode(
                pid,
                "romantic",
                "I want something real from you — how do you really feel about us?",
            ),
            inputs=[persona_id_state],
            outputs=chip_outputs,
        ).then(handle_chat, inputs=chat_inputs, outputs=chat_outputs)

        chip_seductive.click(
            lambda pid: handle_quick_mode(
                pid,
                "seductive",
                "Come closer. Say something that would make me lose focus on everything else.",
            ),
            inputs=[persona_id_state],
            outputs=chip_outputs,
        ).then(handle_chat, inputs=chat_inputs, outputs=chat_outputs)

        chip_playful.click(
            lambda pid: handle_quick_mode(
                pid,
                "playful",
                "Surprise me — say or do something completely unexpected.",
            ),
            inputs=[persona_id_state],
            outputs=chip_outputs,
        ).then(handle_chat, inputs=chat_inputs, outputs=chat_outputs)

        chip_selfie.click(
            lambda: {"text": "Send me a photo of yourself", "files": []},
            outputs=[chat_input],
        ).then(handle_chat, inputs=chat_inputs, outputs=chat_outputs)

        chip_scene.click(
            lambda: {"text": "Describe where we are right now — paint the scene for me.", "files": []},
            outputs=[chat_input],
        ).then(handle_chat, inputs=chat_inputs, outputs=chat_outputs)

        chip_story.click(
            lambda: {"text": "Tell me something from your past that shaped who you are.", "files": []},
            outputs=[chat_input],
        ).then(handle_chat, inputs=chat_inputs, outputs=chat_outputs)

        chip_photo.click(
            lambda: {"text": "I want to see what you look like right now — send me a pic!", "files": []},
            outputs=[chat_input],
        ).then(handle_chat, inputs=chat_inputs, outputs=chat_outputs)

        chip_intense.click(
            handle_intense_chip,
            inputs=[persona_id_state],
            outputs=chip_outputs,
        ).then(handle_chat, inputs=chat_inputs, outputs=chat_outputs)

        suggest_btn.click(
            handle_suggest_replies,
            outputs=[suggestion_dropdown, search_context_box],
        )
        use_suggestion_btn.click(
            handle_use_suggestion,
            inputs=[suggestion_dropdown],
            outputs=[chat_input],
        )

        regen_btn.click(
            handle_regenerate,
            inputs=[search_toggle, reply_as_dropdown, scene_input, multi_respond_cb],
            outputs=[chatbot, search_context_box, chat_summary, suggestion_dropdown],
        )

        export_chat_btn.click(handle_export_chat, outputs=[search_context_box])

        clear_btn.click(
            handle_clear_chat,
            outputs=[chatbot, chat_input, search_context_box, chat_summary, suggestion_dropdown],
        )

        gen_btn.click(
            handle_generate_image,
            inputs=[persona_id_state, img_prompt, aspect_input, img_ref_upload],
            outputs=[result_image, img_status],
        )

        img_search_btn.click(
            handle_search_images,
            inputs=[img_search_query],
            outputs=[img_gallery, img_status],
        )

        web_search_btn.click(
            handle_web_search,
            inputs=[search_query],
            outputs=[web_results],
        )

    return demo


def _pick_port(preferred: int = 7860, attempts: int = 10) -> int:
    from remote import pick_port as _remote_pick_port

    return _remote_pick_port(preferred)


def _gradio_theme():
    return gr.themes.Base(
        primary_hue="violet",
        secondary_hue="pink",
        neutral_hue="slate",
    ).set(
        body_background_fill="*neutral_950",
        block_background_fill="rgba(20,12,35,0.7)",
        block_border_color="rgba(167,139,250,0.2)",
        block_label_text_color="*neutral_200",
        body_text_color="*neutral_100",
        button_primary_background_fill="linear-gradient(135deg, #7c3aed, #a855f7)",
        button_primary_background_fill_hover="linear-gradient(135deg, #6d28d9, #9333ea)",
    )


def launch_app(*, background: bool = False, open_browser: bool | None = None) -> int:
    """Start the Gradio server. Returns the port used."""
    from remote import launch_and_record, print_urls

    if open_browser is None:
        open_browser = not background

    demo = build_ui()
    urls, _ = launch_and_record(
        demo,
        theme=_gradio_theme(),
        css=STUDIO_CSS,
        quiet=background,
        background=background,
        inbrowser=open_browser,
        block_forever=True,
    )
    if not background:
        print_urls(urls)
        print("Tip: run `install-background.bat` to auto-start + access from other devices.\n")
    return urls.port


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Persona Studio")
    parser.add_argument("--background", action="store_true", help="Run without foreground console hints")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open a browser tab")
    args = parser.parse_args()
    launch_app(background=args.background, open_browser=not args.no_browser)