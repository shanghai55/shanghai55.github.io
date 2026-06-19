"""Export chat transcripts and persona data."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR
from memory import message_to_text

EXPORTS_DIR = DATA_DIR / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def chat_to_markdown(
    history: list[dict],
    *,
    title: str = "Persona Studio Chat",
    persona_names: list[str] | None = None,
) -> str:
    """Convert chat history to a readable Markdown transcript."""
    lines = [
        f"# {title}",
        "",
        f"_Exported {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
    ]
    if persona_names:
        lines.append(f"_Characters: {', '.join(persona_names)}_")
    lines.extend(["", "---", ""])

    for entry in history:
        role = entry.get("role", "user")
        speaker = entry.get("speaker", "")
        content = entry.get("content", "")

        if isinstance(content, list):
            text_parts: list[str] = []
            images: list[str] = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict) and item.get("path"):
                    images.append(item["path"])
            body = " ".join(text_parts).strip()
            if images:
                img_note = f" _[{len(images)} image(s)]_"
                body = (body + img_note).strip() if body else f"_[{len(images)} image(s)]_"
        else:
            body = str(content).strip()

        if not body:
            continue

        if role == "assistant" and speaker:
            lines.append(f"**{speaker}:** {body}")
        elif role == "user":
            lines.append(f"**You:** {body}")
        else:
            lines.append(body)
        lines.append("")

    return "\n".join(lines).strip()


def chat_to_plaintext(history: list[dict]) -> str:
    """Simple plain-text transcript."""
    lines: list[str] = []
    for entry in history:
        line = message_to_text(entry)
        if line:
            lines.append(line)
    return "\n".join(lines)


def save_chat_export(
    history: list[dict],
    *,
    session_key: str,
    persona_names: list[str] | None = None,
    fmt: str = "md",
) -> str:
    """Save chat export to disk and return the file path."""
    title = "Persona Studio Chat"
    if persona_names:
        title = f"Chat with {', '.join(persona_names)}"

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_key = session_key[:24].replace("/", "_")
    ext = "md" if fmt == "md" else "txt"
    dest = EXPORTS_DIR / f"chat_{safe_key}_{timestamp}.{ext}"

    if fmt == "md":
        content = chat_to_markdown(history, title=title, persona_names=persona_names)
    else:
        content = chat_to_plaintext(history)

    dest.write_text(content, encoding="utf-8")
    return str(dest.resolve())


def save_persona_export(persona_id: str, json_text: str) -> str:
    """Save persona JSON export to disk."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = EXPORTS_DIR / f"persona_{persona_id[:12]}_{timestamp}.json"
    dest.write_text(json_text, encoding="utf-8")
    return str(dest.resolve())