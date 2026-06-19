"""
Remote access — LAN URLs, public tunnels, and multi-device links.

While your PC is ON: phone/tablet/other PCs can use the LAN or public link.
While your PC is OFF: deploy with Docker to a cloud server (see Dockerfile).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import (
    DATA_DIR,
    PERSONA_BIND_HOST,
    PERSONA_CLOUD,
    PERSONA_PORT,
    PERSONA_REMOTE,
    PERSONA_SHARE,
    PERSONA_TUNNEL,
    get_public_url,
)

URLS_FILE = DATA_DIR / "persona-studio.urls.json"
_TUNNEL_PROC: subprocess.Popen | None = None
_NGROK_TUNNEL = None


@dataclass
class AccessUrls:
    port: int
    local: str
    lan: str | None = None
    public: str | None = None
    public_type: str = "none"
    custom: str | None = None
    updated_at: str = ""

    @property
    def best(self) -> str:
        """Best URL for bookmarks / other devices."""
        if self.custom:
            return self.custom
        if self.public:
            return self.public
        if self.lan:
            return self.lan
        return self.local

    def to_dict(self) -> dict:
        return asdict(self)


def get_lan_ip() -> str | None:
    """Primary LAN IPv4 address for this machine."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return None


def build_urls(port: int, public: str | None = None, public_type: str = "none") -> AccessUrls:
    local = f"http://127.0.0.1:{port}"
    lan_ip = get_lan_ip()
    lan = f"http://{lan_ip}:{port}" if lan_ip else None
    custom = get_public_url()
    if PERSONA_CLOUD and custom and not public:
        public = custom
        public_type = "cloud"
    return AccessUrls(
        port=port,
        local=local,
        lan=lan,
        public=public,
        public_type=public_type,
        custom=custom,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def save_urls(urls: AccessUrls) -> None:
    URLS_FILE.write_text(
        json.dumps(urls.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_urls() -> AccessUrls | None:
    if not URLS_FILE.exists():
        return None
    try:
        data = json.loads(URLS_FILE.read_text(encoding="utf-8"))
        return AccessUrls(**{k: v for k, v in data.items() if k in AccessUrls.__dataclass_fields__})
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def get_best_url(port: int | None = None) -> str:
    saved = load_urls()
    if saved:
        return saved.best
    port = port or PERSONA_PORT
    return build_urls(port).best


def port_is_listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def pick_port(preferred: int | None = None, bind_host: str | None = None) -> int:
    preferred = preferred or PERSONA_PORT
    bind = bind_host or PERSONA_BIND_HOST
    for offset in range(10):
        port = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((bind, port))
                return port
            except OSError:
                continue
    return preferred


def _start_ngrok_tunnel(port: int) -> str | None:
    global _NGROK_TUNNEL
    token = os.getenv("NGROK_AUTHTOKEN") or os.getenv("PERSONA_NGROK_TOKEN")
    if not token:
        return None
    try:
        from pyngrok import conf, ngrok

        conf.get_default().auth_token = token
        _NGROK_TUNNEL = ngrok.connect(port, "http", bind_tls=True)
        return _NGROK_TUNNEL.public_url
    except Exception:
        return None


def _start_cloudflared_tunnel(port: int) -> str | None:
    global _TUNNEL_PROC
    for cmd in ("cloudflared", "cloudflared.exe"):
        try:
            proc = subprocess.Popen(
                [cmd, "tunnel", "--url", f"http://127.0.0.1:{port}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            _TUNNEL_PROC = proc
            if not proc.stdout:
                return None
            for _ in range(80):
                line = proc.stdout.readline()
                if not line:
                    break
                if "https://" in line and "trycloudflare.com" in line:
                    for part in line.split():
                        if part.startswith("https://") and "trycloudflare.com" in part:
                            return part.strip()
            return None
        except OSError:
            continue
    return None


def start_public_tunnel(port: int) -> tuple[str | None, str]:
    """Start a public HTTPS tunnel. Returns (url, tunnel_type)."""
    if not PERSONA_REMOTE or PERSONA_CLOUD:
        return None, "none"

    mode = PERSONA_TUNNEL.lower()
    if mode == "ngrok" or os.getenv("NGROK_AUTHTOKEN") or os.getenv("PERSONA_NGROK_TOKEN"):
        url = _start_ngrok_tunnel(port)
        if url:
            return url, "ngrok"

    if mode == "cloudflare":
        url = _start_cloudflared_tunnel(port)
        if url:
            return url, "cloudflare"

    if PERSONA_SHARE or mode == "gradio":
        return None, "gradio"

    return None, "none"


def stop_public_tunnel() -> None:
    global _TUNNEL_PROC, _NGROK_TUNNEL
    if _NGROK_TUNNEL is not None:
        try:
            from pyngrok import ngrok

            ngrok.disconnect(_NGROK_TUNNEL.public_url)
        except Exception:
            pass
        _NGROK_TUNNEL = None
    if _TUNNEL_PROC is not None:
        try:
            _TUNNEL_PROC.terminate()
        except OSError:
            pass
        _TUNNEL_PROC = None


def gradio_launch_kwargs(*, quiet: bool = False, background: bool = False) -> dict[str, Any]:
    use_share = (
        PERSONA_REMOTE
        and PERSONA_SHARE
        and not PERSONA_CLOUD
        and PERSONA_TUNNEL in ("gradio", "auto", "")
    )
    return dict(
        server_name=PERSONA_BIND_HOST,
        server_port=None,
        share=use_share,
        show_error=not background,
        quiet=quiet,
        app_kwargs={"log_config": None},
    )


def launch_and_record(
    demo,
    *,
    theme,
    css: str,
    port: int | None = None,
    quiet: bool = False,
    background: bool = False,
    inbrowser: bool = False,
    block_forever: bool = True,
) -> tuple[AccessUrls, Any]:
    """
    Launch Gradio, start optional tunnel, save URLs.
    Returns (AccessUrls, launch_result).
    """
    import time

    port = port or pick_port()
    kwargs = gradio_launch_kwargs(quiet=quiet, background=background)
    kwargs.update(
        server_port=port,
        inbrowser=inbrowser,
        prevent_thread_lock=True,
        theme=theme,
        css=css,
    )

    holder: dict = {}

    def _run_server() -> None:
        result = demo.launch(**kwargs)
        holder["result"] = result
        if isinstance(result, tuple) and len(result) >= 3 and result[2]:
            holder["share"] = result[2]
        while True:
            time.sleep(3600)

    thread = threading.Thread(target=_run_server, name="gradio-server", daemon=True)
    thread.start()

    for _ in range(60):
        if port_is_listening(port):
            break
        time.sleep(0.5)

    time.sleep(2)
    share_url = holder.get("share")
    public, public_type = start_public_tunnel(port)
    if not public and share_url:
        public = share_url
        public_type = "gradio"

    urls = build_urls(port, public=public, public_type=public_type)
    save_urls(urls)

    if block_forever:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            stop_public_tunnel()

    return urls, holder.get("result")


def format_urls_markdown(urls: AccessUrls) -> str:
    lines = [
        "**Your Persona Studio links** (use on phone, tablet, or another PC):",
        f"- **This device:** [{urls.local}]({urls.local})",
    ]
    if urls.lan:
        lines.append(f"- **Same Wi‑Fi / LAN:** [{urls.lan}]({urls.lan})")
    if urls.public:
        label = {
            "gradio": "Public (Gradio)",
            "ngrok": "Public (ngrok)",
            "cloudflare": "Public (Cloudflare)",
            "cloud": "Cloud — 24/7, PC can be off",
        }.get(urls.public_type, "Public")
        lines.append(f"- **{label}:** [{urls.public}]({urls.public})")
    if urls.custom and urls.custom != urls.public:
        lines.append(f"- **Your custom URL:** [{urls.custom}]({urls.custom})")
    if PERSONA_CLOUD:
        lines.append("_Runs in the cloud — your laptop can be off. Bookmark the cloud link above._")
    elif not urls.public:
        lines.append(
            "_Tip: run `deploy-cloud.bat` to host 24/7 in the cloud, or set `NGROK_AUTHTOKEN` for a temporary public link._"
        )
    else:
        lines.append(
            "_Note: temporary public links stop when this PC shuts down. Run `deploy-cloud.bat` for always-on access._"
        )
    return "\n".join(lines)


def print_urls(urls: AccessUrls) -> None:
    print("\n" + "=" * 56)
    print("  Persona Studio — open from ANY device")
    print("=" * 56)
    print(f"  This PC:     {urls.local}")
    if urls.lan:
        print(f"  Same Wi-Fi:  {urls.lan}")
    if urls.public:
        print(f"  Anywhere:    {urls.public}")
    if urls.custom:
        print(f"  Custom:      {urls.custom}")
    print(f"  Bookmark:    {urls.best}")
    print("=" * 56 + "\n")