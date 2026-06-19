"""Save user-uploaded or pasted reference images."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from config import UPLOADS_DIR

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".heif"}


def _extract_paths(item) -> list[str]:
    """Pull file paths from Gradio Image/File/Gallery values."""
    if item is None:
        return []

    if isinstance(item, (list, tuple)):
        # Plain list of file paths: [path1, path2, ...]
        if (
            len(item) >= 1
            and all(isinstance(x, str) for x in item)
            and all(Path(x).exists() for x in item)
        ):
            return [str(Path(x).resolve()) for x in item]

        # Gallery item: (filepath, caption)
        if (
            len(item) == 2
            and isinstance(item[0], str)
            and Path(item[0]).exists()
            and not (isinstance(item[1], str) and Path(item[1]).exists())
        ):
            return [str(Path(item[0]).resolve())]

        paths: list[str] = []
        for sub in item:
            paths.extend(_extract_paths(sub))
        return paths

    if isinstance(item, str):
        return [item] if Path(item).exists() else []

    if isinstance(item, dict):
        candidate = item.get("path") or item.get("name")
        if candidate and Path(candidate).exists():
            return [str(candidate)]
        return []

    # Gradio FileData / similar objects
    for attr in ("path", "name"):
        candidate = getattr(item, attr, None)
        if candidate and Path(candidate).exists():
            return [str(candidate)]

    return []


def collect_upload_paths(*items) -> list[str]:
    """Normalize Gradio Image/File/Gallery outputs into local file paths."""
    paths: list[str] = []
    seen: set[str] = set()
    for item in items:
        for path in _extract_paths(item):
            resolved = str(Path(path).resolve())
            if resolved not in seen:
                seen.add(resolved)
                paths.append(resolved)
    return paths


def save_upload(file_obj) -> str | None:
    """Copy one uploaded/pasted image into persistent uploads storage."""
    paths = collect_upload_paths(file_obj)
    if not paths:
        return None

    src = Path(paths[0])
    if not src.exists():
        return None

    suffix = src.suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        suffix = ".png"

    dest = UPLOADS_DIR / f"{uuid.uuid4().hex[:10]}_reference{suffix}"
    shutil.copy2(src, dest)
    return str(dest.resolve())


def save_upload_many(file_obj, max_count: int | None = None) -> list[str]:
    """Copy multiple images into persistent storage."""
    paths = collect_upload_paths(file_obj)
    if max_count is not None:
        paths = paths[:max_count]

    saved: list[str] = []
    for src_path in paths:
        dest = save_upload(src_path)
        if dest:
            saved.append(dest)
    return saved