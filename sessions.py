"""Persist chat history per character or group."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR
from memory import clear_memory, maybe_update_memory

CHATS_DIR = DATA_DIR / "chats"
LAST_SESSION_FILE = CHATS_DIR / "last_session.json"
CHATS_DIR.mkdir(parents=True, exist_ok=True)


def session_key(active_persona_ids: list[str]) -> str:
    ids = sorted(set(active_persona_ids))
    if not ids:
        return "empty"
    if len(ids) == 1:
        return ids[0]
    return "group_" + "_".join(ids)


def _normalize_messages(messages: list[dict]) -> list[dict]:
    """Ensure chat messages are JSON-serializable and file paths still exist."""
    cleaned: list[dict] = []
    for entry in messages or []:
        role = entry.get("role", "user")
        content = entry.get("content", "")
        speaker = entry.get("speaker")

        if isinstance(content, list):
            normalized_content = []
            for item in content:
                if isinstance(item, str):
                    normalized_content.append(item)
                elif isinstance(item, dict):
                    path = item.get("path") or item.get("file", {}).get("path")
                    if path and Path(path).exists():
                        normalized_content.append(
                            {
                                "path": str(Path(path).resolve()),
                                "mime_type": item.get("mime_type")
                                or item.get("file", {}).get("mime_type")
                                or "image/png",
                            }
                        )
                    elif item.get("type") == "text" and item.get("text"):
                        normalized_content.append(item["text"])
            content = normalized_content if normalized_content else ""

        if not content:
            continue

        msg = {"role": role, "content": content}
        if speaker:
            msg["speaker"] = speaker
        cleaned.append(msg)
    return cleaned


def save_chat(
    active_persona_ids: list[str],
    reply_as_id: str | None,
    messages: list[dict],
    *,
    persona_name: str | None = None,
) -> None:
    """Save chat history for the current character/group."""
    key = session_key(active_persona_ids)
    normalized = _normalize_messages(messages)
    payload = {
        "session_key": key,
        "active_persona_ids": list(active_persona_ids),
        "reply_as_id": reply_as_id,
        "messages": normalized,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (CHATS_DIR / f"{key}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if persona_name and normalized:
        maybe_update_memory(key, normalized, persona_name)
    LAST_SESSION_FILE.write_text(
        json.dumps(
            {
                "session_key": key,
                "active_persona_ids": list(active_persona_ids),
                "reply_as_id": reply_as_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def save_session_meta(
    active_persona_ids: list[str],
    reply_as_id: str | None,
    *,
    scene: str = "",
    multi_respond: bool = False,
    selected_persona_id: str | None = None,
) -> None:
    """Persist UI session metadata beyond chat messages."""
    payload = {
        "session_key": session_key(active_persona_ids),
        "active_persona_ids": list(active_persona_ids),
        "reply_as_id": reply_as_id,
        "scene": scene,
        "multi_respond": multi_respond,
        "selected_persona_id": selected_persona_id,
    }
    LAST_SESSION_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_chat(active_persona_ids: list[str]) -> list[dict]:
    """Load saved chat for the given active character(s)."""
    key = session_key(active_persona_ids)
    path = CHATS_DIR / f"{key}.json"
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _normalize_messages(data.get("messages", []))
    except (json.JSONDecodeError, OSError):
        return []


def restore_last_session() -> dict | None:
    """Restore the last open chat session on app startup."""
    if not LAST_SESSION_FILE.exists():
        return None
    try:
        meta = json.loads(LAST_SESSION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    active_ids = meta.get("active_persona_ids") or []
    reply_as = meta.get("reply_as_id")
    history = load_chat(active_ids) if active_ids else []

    return {
        "active_persona_ids": active_ids,
        "reply_as_id": reply_as,
        "chat_history": history,
        "selected_persona_id": meta.get("selected_persona_id")
        or (active_ids[0] if len(active_ids) == 1 else None),
        "scene": meta.get("scene", ""),
        "multi_respond": bool(meta.get("multi_respond", False)),
    }


def clear_chat(active_persona_ids: list[str]) -> None:
    """Delete saved chat for a session."""
    key = session_key(active_persona_ids)
    path = CHATS_DIR / f"{key}.json"
    if path.exists():
        path.unlink()
    clear_memory(key)