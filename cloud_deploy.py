"""
Deploy Persona Studio to the cloud — runs 24/7 without your laptop.

Usage:
  python cloud_deploy.py
  deploy-cloud.bat
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
import zipfile
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
ENV_FILE = APP_DIR / ".env"
ENV_EXAMPLE = APP_DIR / ".env.example"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd or APP_DIR),
        text=True,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
        check=check,
    )


def _which(name: str) -> str | None:
    return shutil.which(name)


def _pause(msg: str = "Press Enter to continue...") -> None:
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt):
        pass


def _load_api_key() -> str | None:
    key = os.getenv("XAI_API_KEY") or os.getenv("GROK_CODE_XAI_API_KEY")
    if key:
        return key.strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("XAI_API_KEY=") and not line.endswith("your-key-here"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    try:
        from config import get_api_key

        return get_api_key()
    except Exception:
        return None


def _ensure_env_file() -> str | None:
    key = _load_api_key()
    if key:
        return key

    print("\nAn xAI API key is required for cloud deploy.")
    print("Get one at https://console.x.ai\n")
    entered = input("Paste your XAI_API_KEY (or press Enter to skip): ").strip()
    if not entered:
        return None

    lines: list[str] = []
    if ENV_EXAMPLE.exists():
        lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    wrote = False
    for idx, line in enumerate(lines):
        if line.startswith("XAI_API_KEY="):
            lines[idx] = f"XAI_API_KEY={entered}"
            wrote = True
            break
    if not wrote:
        lines.append(f"XAI_API_KEY={entered}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Saved to {ENV_FILE}")
    return entered


def _export_data() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive = APP_DIR / f"persona-studio-data-{stamp}.zip"
    skip_names = {
        "persona-studio.pid",
        "persona-studio.port",
        "persona-studio.log",
        "persona-studio.state.json",
    }
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        if DATA_DIR.exists():
            for path in DATA_DIR.rglob("*"):
                if path.is_file() and path.name not in skip_names:
                    zf.write(path, path.relative_to(APP_DIR))
    print(f"\nExported local data to:\n  {archive}")
    print("Upload/extract this into /app/data on your cloud server to keep personas and chats.")
    return archive


def _docker_available() -> bool:
    try:
        result = _run(["docker", "version"], check=False)
        return result.returncode == 0
    except OSError:
        return False


def _test_local_docker(api_key: str) -> None:
    if not _docker_available():
        print("\nDocker is not installed. Install Docker Desktop: https://www.docker.com/products/docker-desktop/")
        _pause()
        return

    print("\nBuilding and starting Persona Studio locally in Docker...")
    print("This tests the cloud image on your PC (Ctrl+C in Docker logs to stop).\n")
    env = os.environ.copy()
    env["XAI_API_KEY"] = api_key
    subprocess.run(
        ["docker", "compose", "up", "--build"],
        cwd=str(APP_DIR),
        env=env,
        check=False,
    )


def _deploy_railway(api_key: str) -> None:
    railway = _which("railway")
    if not railway:
        print("\nRailway CLI not found.")
        print("Install it, then run this again:")
        print("  PowerShell: iwr -useb https://railway.app/install.ps1 | iex")
        print("  Or visit:   https://docs.railway.com/guides/cli\n")
        if input("Open Railway signup in browser? [Y/n] ").strip().lower() != "n":
            webbrowser.open("https://railway.com/new")
        _pause()
        return

    print("\n--- Railway deploy (24/7, ~$5/mo after free trial) ---\n")
    steps = [
        (["railway", "login"], "Log into Railway in your browser"),
        (["railway", "init", "--name", "persona-studio"], "Create/link a Railway project"),
        (["railway", "variables", "set", f"XAI_API_KEY={api_key}"], "Set API key"),
        (["railway", "variables", "set", "PERSONA_CLOUD=true"], "Enable cloud mode"),
        (["railway", "variables", "set", "PERSONA_SHARE=false"], "Disable temp tunnels"),
        (["railway", "up", "--detach"], "Deploy this folder to the cloud"),
        (["railway", "domain"], "Generate your public URL"),
    ]

    for cmd, label in steps:
        print(f"\n>> {label}")
        result = subprocess.run(cmd, cwd=str(APP_DIR), check=False)
        if result.returncode != 0 and cmd[1] != "domain":
            print(f"Step failed (exit {result.returncode}). Fix the issue above and re-run:")
            print(f"  {' '.join(cmd)}")
            _pause()
            return

    print("\nDeploy complete. Your app URL:")
    subprocess.run(["railway", "domain"], cwd=str(APP_DIR), check=False)
    print("\nBookmark that link — it works even when your laptop is off.")
    _pause()


def _deploy_render(api_key: str) -> None:
    print("\n--- Render deploy (24/7, free tier available) ---\n")
    print("1. Push this folder to a GitHub repo (private is fine)")
    print("2. Go to https://dashboard.render.com/select-repo?type=blueprint")
    print("3. Connect the repo — Render reads render.yaml automatically")
    print("4. When prompted, set environment variable:")
    print(f"     XAI_API_KEY = {api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else f"     XAI_API_KEY = (your key)")
    print("5. Click Apply — Render builds the Docker image and gives you a URL like:")
    print("     https://persona-studio.onrender.com")
    print("\nThat URL works 24/7. Your laptop can be off.\n")

    if input("Open Render dashboard in browser? [Y/n] ").strip().lower() != "n":
        webbrowser.open("https://dashboard.render.com/select-repo?type=blueprint")
    _pause()


def _deploy_vps(api_key: str) -> None:
    print("\n--- VPS / home server (Docker, 24/7) ---\n")
    print("Rent any small Linux server (DigitalOcean, Linode, Hetzner, Oracle free tier).")
    print("Copy this folder to the server, then run:\n")
    print("  export XAI_API_KEY='your-key'")
    print("  docker compose up -d --build")
    print("\nOpen http://YOUR_SERVER_IP:7860 from any device.")
    print("Add a reverse proxy + HTTPS (Caddy/nginx) for a proper domain.\n")
    print(f"API key to use: {api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "")
    _pause()


def _print_menu() -> None:
    print("\n" + "=" * 58)
    print("  Persona Studio — Cloud Deploy (24/7, laptop can be OFF)")
    print("=" * 58)
    print("\nLocal mode only works while this PC is running.")
    print("Cloud deploy hosts the app on a server that stays on.\n")
    print("  1) Railway   — easiest, deploy from this folder (~5 min)")
    print("  2) Render    — free tier, needs GitHub repo")
    print("  3) VPS       — any Linux server + Docker")
    print("  4) Test Docker locally (verify before cloud)")
    print("  5) Export data (personas/chats) for cloud migration")
    print("  6) Quit")


def main() -> None:
    api_key = _ensure_env_file()
    if not api_key:
        print("\nCannot deploy without XAI_API_KEY.")
        print("Set it in .env or:  $env:XAI_API_KEY = 'xai-...'")
        _pause()
        return

    while True:
        _print_menu()
        choice = input("\nChoose [1-6]: ").strip()
        if choice == "1":
            _deploy_railway(api_key)
        elif choice == "2":
            _deploy_render(api_key)
        elif choice == "3":
            _deploy_vps(api_key)
        elif choice == "4":
            _test_local_docker(api_key)
        elif choice == "5":
            _export_data()
            _pause()
        elif choice in ("6", "q", "quit", "exit"):
            break
        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main()