"""
Persona Studio — Web server for index.html / style.css / script.js

Serves the static frontend and a REST API backed by the same Python modules
as app.py (Gradio UI).

Run from persona-studio folder:
  python web_server.py
  python web_server.py --port 8080 --open
"""

from __future__ import annotations

import argparse
import json
import tempfile
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from chat import chat_multi, generate_suggested_replies, regenerate_last_response
from config import DATA_DIR, GENERATED_DIR, MULTI_RESPOND_DEFAULT, PERSONA_PORT, UPLOADS_DIR, get_api_key
from memory import get_memory_status
from persona import (
    MAX_REFERENCE_IMAGES,
    MOOD_LABELS,
    MOOD_PRESETS,
    RELATIONSHIP_LABELS,
    RELATIONSHIP_PRESETS,
    PersonaProfile,
    add_reference_image,
    create_persona,
    delete_persona,
    duplicate_persona,
    list_personas,
    load_persona,
    mood_choices,
    relationship_choices,
    save_persona,
)
from remote import build_urls, load_urls, port_is_listening
from sessions import clear_chat as clear_saved_chat
from sessions import load_chat, restore_last_session, save_chat, save_session_meta, session_key
from uploads import save_upload

APP_DIR = Path(__file__).resolve().parent
WEB_PORT = int(__import__("os").getenv("PERSONA_WEB_PORT", "8080"))

_state: dict[str, Any] = {
    "active_persona_ids": [],
    "reply_as_id": None,
    "chat_history": [],
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
    return f"Mode: {mood} · {rel} · {profile.intensity}/10"


def _ref_count_label(profile: PersonaProfile | None) -> str:
    if not profile:
        return f"0/{MAX_REFERENCE_IMAGES} references"
    return f"{len(profile.reference_images)}/{MAX_REFERENCE_IMAGES} references"


def _persona_summary(p: PersonaProfile) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "persona_type": p.persona_type,
        "mood": p.mood,
        "relationship": p.relationship,
        "intensity": p.intensity,
        "reference_images": p.reference_images,
        "language_mode": p.language_mode,
    }


def _session_payload() -> dict:
    active = _active_profiles()
    selected = load_persona(_state["selected_persona_id"]) if _state.get("selected_persona_id") else None
    reply_profile = next((p for p in active if p.id == _state.get("reply_as_id")), active[0] if active else None)
    key = session_key(_state["active_persona_ids"])

    vibe_status = ""
    if selected:
        hint = MOOD_PRESETS.get(selected.mood, "")
        vibe_status = f"{selected.name} — {_mood_badge(selected.mood)} · intensity {selected.intensity}/10"
        if hint:
            vibe_status += f" — {hint[:140]}"

    return {
        "personas": [_persona_summary(p) for p in list_personas()],
        "active_persona_ids": _state["active_persona_ids"],
        "active_personas": [_persona_summary(p) for p in active],
        "selected_persona_id": _state.get("selected_persona_id"),
        "selected_persona": selected.to_dict() if selected else None,
        "in_chat": _state.get("selected_persona_id") in _state["active_persona_ids"],
        "reply_as_id": _state.get("reply_as_id"),
        "reply_as_name": reply_profile.name if reply_profile else None,
        "chat_history": _state["chat_history"],
        "scene": _state.get("scene", ""),
        "multi_respond": _state.get("multi_respond", False),
        "mood": selected.mood if selected else "default",
        "relationship": selected.relationship if selected else "friend",
        "intensity": selected.intensity if selected else 5,
        "mood_choices": mood_choices(),
        "relationship_choices": relationship_choices(),
        "vibe_line": _vibe_line(reply_profile),
        "vibe_status": vibe_status,
        "memory_line": get_memory_status(key, _state.get("chat_history") or []),
        "ref_count_label": _ref_count_label(selected),
    }


def _chat_result(context: str | None = None, suggestions: list[str] | None = None) -> dict:
    payload = _session_payload()
    payload["context"] = context
    payload["suggestions"] = suggestions or []
    return payload


app = FastAPI(title="Persona Studio Web API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
def api_status() -> dict:
    urls = load_urls()
    url_dict = urls.to_dict() if urls else build_urls(WEB_PORT).to_dict()
    url_dict["local"] = f"http://127.0.0.1:{WEB_PORT}"
    gradio_url = f"http://127.0.0.1:{PERSONA_PORT}" if port_is_listening(PERSONA_PORT) else None
    return {
        "api_connected": bool(get_api_key()),
        "urls": url_dict,
        "gradio_url": gradio_url,
        "web_port": WEB_PORT,
    }


@app.get("/api/session")
def api_session() -> dict:
    return _session_payload()


@app.post("/api/session/select")
def api_select_persona(body: dict) -> dict:
    persona_id = body.get("persona_id")
    if not persona_id:
        raise HTTPException(400, "persona_id required")

    profile = load_persona(persona_id)
    if not profile:
        raise HTTPException(404, "Character not found")

    _persist_chat()
    _state["selected_persona_id"] = persona_id
    _state["active_persona_ids"] = [persona_id]
    _state["reply_as_id"] = persona_id
    _state["chat_history"] = load_chat([persona_id])
    _persist_chat()
    return _session_payload()


@app.post("/api/session/toggle-chat")
def api_toggle_chat(body: dict) -> dict:
    persona_id = _state.get("selected_persona_id")
    if not persona_id:
        raise HTTPException(400, "Select a character first")

    in_chat = bool(body.get("in_chat"))
    _persist_chat()

    if in_chat and persona_id not in _state["active_persona_ids"]:
        _state["active_persona_ids"].append(persona_id)
        if not _state["reply_as_id"]:
            _state["reply_as_id"] = persona_id
    elif not in_chat and persona_id in _state["active_persona_ids"]:
        _state["active_persona_ids"] = [p for p in _state["active_persona_ids"] if p != persona_id]
        if _state["reply_as_id"] == persona_id:
            _state["reply_as_id"] = _state["active_persona_ids"][0] if _state["active_persona_ids"] else None

    _state["chat_history"] = load_chat(_state["active_persona_ids"])
    _persist_chat()
    return _session_payload()


@app.post("/api/session/vibe")
def api_session_vibe(body: dict) -> dict:
    persona_id = _state.get("selected_persona_id")
    profile = load_persona(persona_id) if persona_id else None
    if not profile:
        raise HTTPException(400, "Select a character first")

    mood = body.get("mood")
    relationship = body.get("relationship")
    intensity = body.get("intensity", profile.intensity)

    if mood in MOOD_PRESETS:
        profile.mood = mood
    if relationship in RELATIONSHIP_PRESETS:
        profile.relationship = relationship
    profile.intensity = max(1, min(10, int(intensity or profile.intensity)))
    save_persona(profile)

    hint = MOOD_PRESETS.get(profile.mood, "")
    vibe_status = f"{profile.name} — {_mood_badge(profile.mood)} · intensity {profile.intensity}/10"
    if hint:
        vibe_status += f" — {hint[:140]}"

    return {"vibe_status": vibe_status, **_session_payload()}


@app.post("/api/session/scene")
def api_session_scene(body: dict) -> dict:
    _state["scene"] = body.get("scene", "")
    _persist_chat()
    return _session_payload()


@app.post("/api/session/reply-as")
def api_reply_as(body: dict) -> dict:
    _state["reply_as_id"] = body.get("reply_as_id")
    _persist_chat()
    return _session_payload()


@app.get("/api/personas")
def api_list_personas() -> dict:
    return {"personas": [_persona_summary(p) for p in list_personas()]}


@app.post("/api/personas")
def api_create_persona(body: dict) -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Enter a character name.")

    profile, _research = create_persona(
        name=name,
        persona_type=body.get("persona_type", "anime"),
        language_mode=body.get("language_mode", "auto"),
        auto_research=bool(body.get("auto_research", True)),
    )

    _state["selected_persona_id"] = profile.id
    _state["active_persona_ids"] = [profile.id]
    _state["reply_as_id"] = profile.id
    _state["chat_history"] = []
    _persist_chat()
    return _session_payload()


@app.delete("/api/personas/{persona_id}")
def api_delete_persona(persona_id: str) -> dict:
    profile = load_persona(persona_id)
    if not profile:
        raise HTTPException(404, "Character not found")

    delete_persona(persona_id)
    if persona_id in _state["active_persona_ids"]:
        _state["active_persona_ids"] = [p for p in _state["active_persona_ids"] if p != persona_id]
    if _state.get("selected_persona_id") == persona_id:
        _state["selected_persona_id"] = None
    if _state.get("reply_as_id") == persona_id:
        _state["reply_as_id"] = _state["active_persona_ids"][0] if _state["active_persona_ids"] else None
    _persist_chat()
    return _session_payload()


@app.post("/api/personas/{persona_id}/duplicate")
def api_duplicate_persona(persona_id: str) -> dict:
    clone = duplicate_persona(persona_id)
    if not clone:
        raise HTTPException(400, "Could not duplicate.")
    return _session_payload()


@app.post("/api/personas/{persona_id}/references")
async def api_add_references(persona_id: str, images: list[UploadFile] = File(...)) -> dict:
    profile = load_persona(persona_id)
    if not profile:
        raise HTTPException(404, "Character not found")

    if not images:
        raise HTTPException(400, "No images uploaded.")

    remaining = MAX_REFERENCE_IMAGES - len(profile.reference_images)
    if remaining <= 0:
        raise HTTPException(400, f"Already at {MAX_REFERENCE_IMAGES} references.")

    saved_count = 0
    for upload in images[:remaining]:
        suffix = Path(upload.filename or "upload.png").suffix or ".png"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await upload.read())
            tmp_path = tmp.name
        stored = save_upload(tmp_path)
        Path(tmp_path).unlink(missing_ok=True)
        if not stored:
            continue
        profile, msg = add_reference_image(profile, stored)
        if "saved" in msg.lower():
            saved_count += 1

    profile = load_persona(profile.id)
    count = len(profile.reference_images) if profile else 0
    return {
        "message": f"Saved {saved_count} photo(s). ({count}/{MAX_REFERENCE_IMAGES})",
        **_session_payload(),
    }


async def _handle_chat(
    message: str,
    use_search: bool,
    reply_as_id: str | None,
    scene: str,
    multi_respond: bool,
    mood: str | None,
    images: list[UploadFile] | None,
) -> dict:
    active = _active_profiles()
    if not active:
        raise HTTPException(400, "Add at least one character to the chat.")

    if mood and _state.get("selected_persona_id"):
        profile = load_persona(_state["selected_persona_id"])
        if profile and mood in MOOD_PRESETS:
            profile.mood = mood
            save_persona(profile)

    user_images: list[str] = []
    if images:
        for upload in images:
            suffix = Path(upload.filename or "upload.png").suffix or ".png"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(await upload.read())
                tmp_path = tmp.name
            stored = save_upload(tmp_path)
            Path(tmp_path).unlink(missing_ok=True)
            if stored:
                user_images.append(stored)

    text = (message or "").strip()
    if not text and not user_images:
        raise HTTPException(400, "Message or image required.")

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
            user_images=user_images or None,
            session_key=session_key(_state["active_persona_ids"]),
            scene_override=_state.get("scene") or None,
            multi_respond=bool(multi_respond),
        )
    except Exception as exc:
        raise HTTPException(500, f"Chat error: {exc}") from exc

    _state["chat_history"] = updated
    _persist_chat()

    status_note = ""
    if user_images:
        status_note = f"You sent {len(user_images)} photo(s) — {speaker} can see them."
    if photo_status:
        status_note = (
            (status_note + "\n" if status_note else "")
            + (f"Photo sent by {speaker}. {photo_status}" if image_path else f"Photo not sent: {photo_status}")
        )
    combined_ctx = "\n\n".join(p for p in [search_ctx, status_note] if p) or None
    suggestions = generate_suggested_replies(active, _state["chat_history"])
    return _chat_result(combined_ctx, suggestions)


@app.post("/api/chat")
async def api_chat(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        images = [f for f in form.getlist("images") if hasattr(f, "read")]
        return await _handle_chat(
            message=str(form.get("message", "")),
            use_search=str(form.get("use_search", "")).lower() in ("true", "1", "yes"),
            reply_as_id=form.get("reply_as_id") or None,
            scene=str(form.get("scene", "")),
            multi_respond=str(form.get("multi_respond", "")).lower() in ("true", "1", "yes"),
            mood=form.get("mood") or None,
            images=images or None,
        )

    body = await request.json()
    return await _handle_chat(
        message=body.get("message", ""),
        use_search=bool(body.get("use_search")),
        reply_as_id=body.get("reply_as_id"),
        scene=body.get("scene", ""),
        multi_respond=bool(body.get("multi_respond")),
        mood=body.get("mood"),
        images=None,
    )


@app.post("/api/chat/regenerate")
def api_regenerate(body: dict) -> dict:
    active = _active_profiles()
    if not active:
        raise HTTPException(400, "Add at least one character first.")

    _state["scene"] = body.get("scene", "")
    try:
        speaker, reply, updated, search_ctx, image_path, photo_status = regenerate_last_response(
            active_profiles=active,
            history=_state["chat_history"],
            reply_as_id=body.get("reply_as_id") or _state.get("reply_as_id"),
            use_web_search=bool(body.get("use_search")),
            session_key=session_key(_state["active_persona_ids"]),
            scene_override=_state.get("scene") or None,
        )
    except Exception as exc:
        raise HTTPException(500, f"Regenerate failed: {exc}") from exc

    _state["chat_history"] = updated
    _persist_chat()

    status_note = ""
    if photo_status:
        status_note = f"Photo by {speaker}: {photo_status}" if image_path else photo_status
    combined_ctx = "\n\n".join(p for p in [search_ctx, status_note] if p) or None
    suggestions = generate_suggested_replies(active, _state["chat_history"])
    return _chat_result(combined_ctx, suggestions)


@app.post("/api/chat/clear")
def api_clear_chat() -> dict:
    _state["chat_history"] = []
    if _state["active_persona_ids"]:
        clear_saved_chat(_state["active_persona_ids"])
    _persist_chat()
    return _session_payload()


@app.get("/api/chat/suggestions")
def api_suggestions() -> dict:
    active = _active_profiles()
    if not active:
        return {"suggestions": [], "message": "Add characters to get suggestions."}
    suggestions = generate_suggested_replies(active, _state["chat_history"])
    if not suggestions:
        return {"suggestions": [], "message": "No suggestions right now — chat a bit first."}
    return {"suggestions": suggestions, "message": f"Pick a suggestion ({len(suggestions)} options)."}


@app.get("/api/files/{folder}/{filename}")
def api_serve_file(folder: str, filename: str):
    if folder not in ("uploads", "generated"):
        raise HTTPException(404, "Not found")
    base = UPLOADS_DIR if folder == "uploads" else GENERATED_DIR
    path = (base / filename).resolve()
    if not path.exists() or base.resolve() not in path.parents:
        raise HTTPException(404, "File not found")
    return FileResponse(path)


app.mount("/", StaticFiles(directory=str(APP_DIR), html=True), name="static")


def main() -> None:
    parser = argparse.ArgumentParser(description="Persona Studio Web Server")
    parser.add_argument("--port", type=int, default=WEB_PORT, help="Port for the web UI")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--open", action="store_true", help="Open browser on start")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  Persona Studio Web UI: {url}\n")
    if args.open:
        webbrowser.open(url)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()