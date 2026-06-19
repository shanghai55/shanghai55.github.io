"""Configuration and API key resolution for Persona Studio."""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
PERSONAS_DIR = DATA_DIR / "personas"
UPLOADS_DIR = DATA_DIR / "uploads"
GENERATED_DIR = DATA_DIR / "generated"
IMAGE_STATE_DIR = DATA_DIR / "image_state"
CHATS_DIR = DATA_DIR / "chats"
MEMORY_DIR = DATA_DIR / "memory"
GROK_AUTH_PATH = Path.home() / ".grok" / "auth.json"

XAI_BASE_URL = "https://api.x.ai/v1"
CHAT_MODEL = os.getenv("PERSONA_CHAT_MODEL", "grok-4.3")
CHAT_ENHANCE = os.getenv("PERSONA_CHAT_ENHANCE", "true").lower() in ("1", "true", "yes")
CHAT_MAX_TOKENS = int(os.getenv("PERSONA_CHAT_MAX_TOKENS", "2048"))
CHAT_TEMPERATURE = float(os.getenv("PERSONA_CHAT_TEMPERATURE", "1.0"))
SUGGESTED_REPLIES = os.getenv("PERSONA_SUGGESTED_REPLIES", "true").lower() in ("1", "true", "yes")
MULTI_RESPOND_DEFAULT = os.getenv("PERSONA_MULTI_RESPOND", "false").lower() in ("1", "true", "yes")
IMAGE_MODEL = os.getenv("PERSONA_IMAGE_MODEL", "grok-imagine-image-quality")
IMAGE_RESOLUTION = os.getenv("PERSONA_IMAGE_RESOLUTION", "2k")
MAX_EDIT_IMAGES = 3
IMAGE_CLONE_THRESHOLD = float(os.getenv("PERSONA_IMAGE_CLONE_THRESHOLD", "0.93"))
IMAGE_GENERATE_FIRST = os.getenv("PERSONA_IMAGE_GENERATE_FIRST", "true").lower() in (
    "1",
    "true",
    "yes",
)
IMAGE_CRAFT_PROMPTS = os.getenv("PERSONA_IMAGE_CRAFT_PROMPTS", "false").lower() in (
    "1",
    "true",
    "yes",
)
IMAGE_MAX_MULTI_SLOTS = int(os.getenv("PERSONA_IMAGE_MAX_MULTI_SLOTS", "2"))
IMAGE_MAX_SINGLE_TRIES = int(os.getenv("PERSONA_IMAGE_MAX_SINGLE_TRIES", "3"))

# Long-term conversation memory — up to 10 GB storage budget per installation.
MEMORY_STORAGE_BUDGET_GB = float(os.getenv("PERSONA_MEMORY_GB", "10"))
MEMORY_STORAGE_BUDGET_BYTES = int(MEMORY_STORAGE_BUDGET_GB * 1024**3)
MEMORY_CONTEXT_TOKEN_BUDGET = int(os.getenv("PERSONA_MEMORY_TOKENS", "120000"))
MEMORY_SUMMARIZE_EVERY = int(os.getenv("PERSONA_MEMORY_SUMMARIZE_EVERY", "24"))
MEMORY_KEEP_RECENT = int(os.getenv("PERSONA_MEMORY_KEEP_RECENT", "48"))

# Network / remote access (other devices, phones, cloud)
# 0.0.0.0 = reachable on your Wi-Fi from phone/tablet/other PCs
PERSONA_BIND_HOST = os.getenv("PERSONA_BIND_HOST", os.getenv("PERSONA_HOST", "0.0.0.0"))
PERSONA_HOST = PERSONA_BIND_HOST  # backwards compatible alias
PERSONA_PORT = int(os.getenv("GRADIO_SERVER_PORT", os.getenv("PORT", "7860")))
PERSONA_REMOTE = os.getenv("PERSONA_REMOTE", "true").lower() in ("1", "true", "yes")
PERSONA_SHARE = os.getenv("PERSONA_SHARE", "true").lower() in ("1", "true", "yes")
PERSONA_TUNNEL = os.getenv("PERSONA_TUNNEL", "auto").lower()  # auto | gradio | ngrok | cloudflare | none
PERSONA_CLOUD = os.getenv("PERSONA_CLOUD", "").lower() in ("1", "true", "yes")


def get_public_url() -> str | None:
    """Resolve the public HTTPS URL (cloud hosts set this automatically)."""
    explicit = os.getenv("PERSONA_PUBLIC_URL", "").strip()
    if explicit:
        return explicit if explicit.startswith("http") else f"https://{explicit}"

    render = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if render:
        return render if render.startswith("http") else f"https://{render}"

    railway = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway:
        return railway if railway.startswith("http") else f"https://{railway}"

    fly = os.getenv("FLY_APP_NAME", "").strip()
    if fly:
        return f"https://{fly}.fly.dev"

    return None


PERSONA_PUBLIC_URL = get_public_url() or ""

# Background daemon
PERSONA_BACKGROUND = os.getenv("PERSONA_BACKGROUND", "").lower() in ("1", "true", "yes")
SERVICE_PID_FILE = DATA_DIR / "persona-studio.pid"
SERVICE_PORT_FILE = DATA_DIR / "persona-studio.port"
SERVICE_LOG_FILE = DATA_DIR / "persona-studio.log"
URLS_FILE = DATA_DIR / "persona-studio.urls.json"

for directory in (
    PERSONAS_DIR,
    UPLOADS_DIR,
    GENERATED_DIR,
    IMAGE_STATE_DIR,
    CHATS_DIR,
    MEMORY_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)


def get_api_key() -> str | None:
    """Resolve xAI API key from env or Grok CLI auth."""
    key = os.getenv("XAI_API_KEY") or os.getenv("GROK_CODE_XAI_API_KEY")
    if key:
        return key

    if not GROK_AUTH_PATH.exists():
        return None

    try:
        auth_data = json.loads(GROK_AUTH_PATH.read_text(encoding="utf-8"))
        for entry in auth_data.values():
            if isinstance(entry, dict) and entry.get("key"):
                return entry["key"]
    except (json.JSONDecodeError, OSError):
        return None

    return None