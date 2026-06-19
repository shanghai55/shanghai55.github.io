"""Long-term conversation memory — full chat recall with smart context windows."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from openai import OpenAI

from config import (
    CHATS_DIR,
    CHAT_MODEL,
    MEMORY_CONTEXT_TOKEN_BUDGET,
    MEMORY_DIR,
    MEMORY_KEEP_RECENT,
    MEMORY_STORAGE_BUDGET_BYTES,
    MEMORY_STORAGE_BUDGET_GB,
    MEMORY_SUMMARIZE_EVERY,
    XAI_BASE_URL,
    get_api_key,
)


@dataclass
class MemoryState:
    session_key: str
    message_count: int = 0
    summary: str = ""
    key_facts: list[str] = field(default_factory=list)
    last_summarized_index: int = 0
    storage_bytes: int = 0
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> MemoryState:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        if "key_facts" not in filtered:
            filtered["key_facts"] = []
        return cls(**filtered)


@dataclass
class MemoryContext:
    """What to inject into the next API call."""

    memory_prompt: str
    history_start_index: int
    total_messages: int
    included_messages: int
    using_summary: bool
    storage_bytes: int
    storage_budget_gb: float


def _memory_path(session_key: str) -> Path:
    safe = re.sub(r"[^\w\-]", "_", session_key)
    return MEMORY_DIR / f"{safe}.json"


def _client() -> OpenAI:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "No xAI API key found. Set XAI_API_KEY or sign in with `grok` first."
        )
    return OpenAI(api_key=api_key, base_url=XAI_BASE_URL, timeout=httpx.Timeout(120.0))


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def message_to_text(entry: dict) -> str:
    """Plain-text transcript line for one chat message."""
    role = entry.get("role", "user")
    speaker = entry.get("speaker", "")
    content = entry.get("content", "")

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("path"):
                    parts.append("[shared a photo]")
                elif item.get("type") == "text" and item.get("text"):
                    parts.append(item["text"])
        body = " ".join(parts).strip()
    else:
        body = str(content).strip()

    if not body:
        return ""

    if role == "assistant" and speaker:
        return f"{speaker}: {body}"
    if role == "user":
        return f"User: {body}"
    return body


def estimate_message_tokens(entry: dict) -> int:
    text = message_to_text(entry)
    # Vision messages cost more — budget extra for user images.
    content = entry.get("content", "")
    extra = 0
    if isinstance(content, list):
        extra = sum(800 for item in content if isinstance(item, dict) and item.get("path"))
    return estimate_tokens(text) + extra


def history_transcript(history: list[dict], start: int = 0, end: int | None = None) -> str:
    lines: list[str] = []
    slice_end = end if end is not None else len(history)
    for entry in history[start:slice_end]:
        line = message_to_text(entry)
        if line:
            lines.append(line)
    return "\n".join(lines)


def load_memory_state(session_key: str) -> MemoryState:
    path = _memory_path(session_key)
    if not path.exists():
        return MemoryState(session_key=session_key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return MemoryState.from_dict(data)
    except (json.JSONDecodeError, OSError, TypeError):
        return MemoryState(session_key=session_key)


def save_memory_state(state: MemoryState) -> None:
    state.storage_bytes = compute_session_storage(state.session_key)
    state.updated_at = datetime.now(timezone.utc).isoformat()
    _memory_path(state.session_key).write_text(
        json.dumps(state.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def clear_memory(session_key: str) -> None:
    path = _memory_path(session_key)
    if path.exists():
        path.unlink()


def _referenced_image_bytes(history: list[dict]) -> int:
    total = 0
    seen: set[str] = set()
    for entry in history:
        content = entry.get("content", "")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            path = item.get("path") or item.get("file", {}).get("path")
            if not path or path in seen:
                continue
            seen.add(path)
            p = Path(path)
            if p.exists():
                total += p.stat().st_size
    return total


def compute_session_storage(session_key: str, history: list[dict] | None = None) -> int:
    """Bytes used by this session's chat log, memory file, and referenced images."""
    total = 0
    chat_path = CHATS_DIR / f"{session_key}.json"
    mem_path = _memory_path(session_key)
    if chat_path.exists():
        total += chat_path.stat().st_size
    if mem_path.exists():
        total += mem_path.stat().st_size
    if history is None and chat_path.exists():
        try:
            data = json.loads(chat_path.read_text(encoding="utf-8"))
            history = data.get("messages", [])
        except (json.JSONDecodeError, OSError):
            history = []
    if history:
        total += _referenced_image_bytes(history)
    return total


def format_memory_for_system_prompt(state: MemoryState, persona_name: str) -> str:
    if not state.summary and not state.key_facts:
        return ""

    sections = [
        "CONVERSATION MEMORY (full chat from the start — use this to stay consistent):",
        f"- You have been talking with this user across {state.message_count} messages.",
        "- Treat everything below as real shared history. Reference past topics naturally.",
        "- Do not say you forgot or cannot remember — you remember the whole conversation.",
    ]
    if state.summary:
        sections.extend(["", "STORY SO FAR:", state.summary.strip()])
    if state.key_facts:
        sections.extend(["", "KEY DETAILS TO REMEMBER:"])
        sections.extend(f"- {fact}" for fact in state.key_facts[:20])
    sections.append("")
    return "\n".join(sections)


def _parse_summary_response(text: str) -> tuple[str, list[str]]:
    summary = ""
    facts: list[str] = []

    summary_match = re.search(
        r"SUMMARY:\s*(.+?)(?=KEY FACTS:|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    facts_match = re.search(r"KEY FACTS:\s*(.+)$", text, re.IGNORECASE | re.DOTALL)

    if summary_match:
        summary = summary_match.group(1).strip()
    elif text.strip():
        summary = text.strip()

    if facts_match:
        for line in facts_match.group(1).splitlines():
            cleaned = re.sub(r"^[\-\*\d\.\)\s]+", "", line).strip()
            if cleaned:
                facts.append(cleaned)

    return summary, facts[:20]


def _summarize_chunk(
    persona_name: str,
    existing_summary: str,
    chunk: str,
    message_range: str,
) -> tuple[str, list[str]]:
    prompt = f"""You maintain long-term memory for an ongoing in-character chat.

Character: {persona_name}
Messages being merged: {message_range}

EXISTING MEMORY (from earlier in the conversation):
{existing_summary or "(none yet)"}

NEW CONVERSATION TO MERGE:
{chunk}

Write an updated MEMORY that captures the ENTIRE conversation from the very start.
Keep: names, places, jokes, flirtation, photo exchanges, outfits/scenes requested,
user preferences, promises, emotional beats, Tagalog/Taglish phrases, and plot threads.
Be thorough — this memory replaces re-reading old messages.

Format exactly:
SUMMARY:
(your narrative memory, up to 2000 words)

KEY FACTS:
- (specific detail)
- (another detail)
"""
    client = _client()
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a precise conversation archivist. "
                    "Merge old and new information without losing details."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=4096,
    )
    raw = response.choices[0].message.content or ""
    return _parse_summary_response(raw)


def maybe_update_memory(
    session_key: str,
    history: list[dict],
    persona_name: str,
) -> MemoryState:
    """
    Incrementally summarize older messages so the full conversation is always remembered.
    Everything stays on disk; summaries let the model recall chats that exceed context limits.
    """
    state = load_memory_state(session_key)
    state.message_count = len(history)
    state.storage_bytes = compute_session_storage(session_key, history)

    if len(history) < MEMORY_SUMMARIZE_EVERY:
        save_memory_state(state)
        return state

    unsummarized = len(history) - state.last_summarized_index
    if unsummarized < MEMORY_SUMMARIZE_EVERY:
        save_memory_state(state)
        return state

    summarize_end = max(
        state.last_summarized_index,
        len(history) - MEMORY_KEEP_RECENT,
    )
    if summarize_end <= state.last_summarized_index:
        save_memory_state(state)
        return state

    chunk = history_transcript(history, state.last_summarized_index, summarize_end)
    if not chunk.strip():
        save_memory_state(state)
        return state

    message_range = f"messages {state.last_summarized_index + 1}–{summarize_end}"
    try:
        summary, facts = _summarize_chunk(
            persona_name,
            state.summary,
            chunk,
            message_range,
        )
        if summary:
            state.summary = summary
        if facts:
            merged = list(dict.fromkeys(state.key_facts + facts))
            state.key_facts = merged[:20]
        state.last_summarized_index = summarize_end
    except Exception:
        pass

    save_memory_state(state)
    return state


def build_memory_context(
    session_key: str,
    history: list[dict],
    persona_name: str,
) -> MemoryContext:
    """
    Decide how much verbatim history fits in the model context.
    Older turns are recalled via the rolling memory summary.
    """
    state = maybe_update_memory(session_key, history, persona_name)
    memory_prompt = format_memory_for_system_prompt(state, persona_name)
    memory_tokens = estimate_tokens(memory_prompt)

    remaining_budget = max(8000, MEMORY_CONTEXT_TOKEN_BUDGET - memory_tokens - 4000)

    total_tokens = sum(estimate_message_tokens(entry) for entry in history)
    if total_tokens <= remaining_budget:
        return MemoryContext(
            memory_prompt=memory_prompt,
            history_start_index=0,
            total_messages=len(history),
            included_messages=len(history),
            using_summary=bool(state.summary),
            storage_bytes=state.storage_bytes,
            storage_budget_gb=MEMORY_STORAGE_BUDGET_GB,
        )

    included = 0
    used_tokens = 0
    for entry in reversed(history):
        entry_tokens = estimate_message_tokens(entry)
        if used_tokens + entry_tokens > remaining_budget:
            break
        used_tokens += entry_tokens
        included += 1

    included = max(included, min(MEMORY_KEEP_RECENT, len(history)))
    start_index = len(history) - included

    # Never leave a gap the summary doesn't cover.
    if state.summary and start_index > state.last_summarized_index:
        start_index = min(start_index, state.last_summarized_index)
    if start_index > 0 and not state.summary:
        start_index = 0

    return MemoryContext(
        memory_prompt=memory_prompt,
        history_start_index=start_index,
        total_messages=len(history),
        included_messages=len(history) - start_index,
        using_summary=bool(state.summary) or start_index > 0,
        storage_bytes=state.storage_bytes,
        storage_budget_gb=MEMORY_STORAGE_BUDGET_GB,
    )


def get_memory_status(session_key: str, history: list[dict]) -> str:
    state = load_memory_state(session_key)
    storage = compute_session_storage(session_key, history)
    used_mb = storage / (1024 * 1024)
    budget_gb = MEMORY_STORAGE_BUDGET_GB
    pct = (storage / MEMORY_STORAGE_BUDGET_BYTES * 100) if MEMORY_STORAGE_BUDGET_BYTES else 0

    if state.summary:
        recall = f"summary + last {min(len(history), MEMORY_KEEP_RECENT)} msgs"
    elif len(history) > 0:
        recall = "full verbatim history"
    else:
        recall = "no messages yet"

    return (
        f"**Memory:** {len(history)} messages · {recall} · "
        f"{used_mb:.1f} MB / {budget_gb:.0f} GB ({pct:.2f}%)"
    )