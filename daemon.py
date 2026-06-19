"""
Persona Studio background daemon — runs without a CMD window.

Usage:
  pythonw daemon.py start          # background + system tray (default)
  pythonw daemon.py start --no-tray
  python daemon.py stop
  python daemon.py status
  python daemon.py install         # auto-start at Windows login
  python daemon.py uninstall
  python daemon.py open            # open browser to the app

Double-click start-hidden.vbs for a silent start with no terminal at all.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from config import APP_DIR, DATA_DIR, PERSONA_PORT
from remote import (
    get_best_url,
    load_urls,
    port_is_listening,
    pick_port,
    print_urls,
    start_public_tunnel,
    stop_public_tunnel,
)

PID_FILE = DATA_DIR / "persona-studio.pid"
PORT_FILE = DATA_DIR / "persona-studio.port"
LOG_FILE = DATA_DIR / "persona-studio.log"
STATE_FILE = DATA_DIR / "persona-studio.state.json"
TASK_NAME = "PersonaStudioBackground"
DEFAULT_PORT = PERSONA_PORT

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_SPAWN_FLAGS = CREATE_NO_WINDOW


def _log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def _worker_python() -> str:
    """Python for background workers — prefer python.exe (more reliable than pythonw)."""
    return sys.executable


def _silent_python() -> str:
    """Python with no console — pythonw for double-click / VBS launchers."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        return pid if pid > 0 else None
    except (OSError, ValueError):
        return None


def _process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
            return str(pid) in result.stdout
        except OSError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _cleanup_stale_files() -> None:
    pid = _read_pid()
    if pid and _process_alive(pid):
        return
    for path in (PID_FILE, PORT_FILE):
        if path.exists():
            path.unlink(missing_ok=True)


def _write_state(
    running: bool,
    pid: int | None = None,
    port: int | None = None,
    urls: AccessUrls | None = None,
) -> None:
    saved = urls or (load_urls() if port else None)
    best = saved.best if saved else (get_best_url(port) if port else None)
    payload = {
        "running": running,
        "pid": pid,
        "port": port,
        "url": best,
        "urls": saved.to_dict() if saved else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_port() -> int | None:
    if PORT_FILE.exists():
        try:
            return int(PORT_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            port = data.get("port")
            return int(port) if port else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return None


def get_url() -> str:
    saved = load_urls()
    if saved:
        return saved.best
    return get_best_url(get_port() or DEFAULT_PORT)


def status() -> dict:
    _cleanup_stale_files()
    pid = _read_pid()
    running = bool(pid and _process_alive(pid))
    port = get_port()
    saved = load_urls()
    return {
        "running": running,
        "pid": pid,
        "port": port,
        "url": saved.best if saved else get_best_url(port or DEFAULT_PORT),
        "urls": saved.to_dict() if saved else None,
        "log_file": str(LOG_FILE),
    }


def stop() -> str:
    _cleanup_stale_files()
    pid = _read_pid()
    if not pid:
        return "Persona Studio is not running."

    if not _process_alive(pid):
        _cleanup_stale_files()
        _write_state(False)
        return "Stale lock removed — was not running."

    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    for _ in range(20):
        if not _process_alive(pid):
            break
        time.sleep(0.25)

    stop_public_tunnel()
    _cleanup_stale_files()
    _write_state(False)
    _log(f"Stopped daemon (pid {pid})")
    return f"Stopped Persona Studio (pid {pid})."


def _spawn_background(no_tray: bool = False) -> int:
    """Launch a fully detached worker (survives parent shell closing)."""
    python = _worker_python()
    script = str(APP_DIR / "daemon.py")
    args = [python, script, "run"]
    if no_tray:
        args.append("--no-tray")
    _log(f"--- spawn {datetime.now(timezone.utc).isoformat()} ---")

    try:
        proc = subprocess.Popen(
            args,
            cwd=str(APP_DIR),
            creationflags=_SPAWN_FLAGS,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        _log(f"Spawn failed: {exc}")
        return 0

    if not proc.pid:
        _log("Spawn failed: no PID returned")
        return 0
    return proc.pid


def _find_listening_port() -> int | None:
    for offset in range(10):
        port = DEFAULT_PORT + offset
        if port_is_listening(port):
            return port
    return None


def start(*, no_tray: bool = False, open_browser: bool = False) -> str:
    _cleanup_stale_files()
    current = status()
    if current["running"]:
        url = current["url"]
        if open_browser:
            webbrowser.open(url)
        return f"Already running at {url} (pid {current['pid']})"

    listening = _find_listening_port()
    if listening:
        pid = _read_pid()
        if pid and _process_alive(pid):
            url = get_url()
            if open_browser:
                webbrowser.open(url)
            return f"Already running at {url} (pid {pid})"
        return (
            f"Port {listening} is already in use. "
            f"Run stop.bat or `python daemon.py stop`, then try again."
        )

    pid = _spawn_background(no_tray=no_tray)
    _log(f"Spawned background process (pid {pid}, tray={not no_tray})")
    if not pid:
        return f"Failed to start Persona Studio — see {LOG_FILE}"

    for _ in range(60):
        time.sleep(0.5)
        live = status()
        if live["running"] and live["port"] and port_is_listening(live["port"]):
            url = live["url"]
            if open_browser:
                webbrowser.open(url)
            return f"Persona Studio started at {url} (pid {live['pid']})"
        if live["running"]:
            return f"Persona Studio starting… pid {live['pid']}. Check {get_url()} in a moment."

    return (
        f"Start requested (spawn pid {pid}). Server may still be loading — "
        f"check {LOG_FILE} or run: python daemon.py status"
    )


def _save_runtime(pid: int, port: int, urls: AccessUrls | None = None) -> None:
    PID_FILE.write_text(str(pid), encoding="utf-8")
    PORT_FILE.write_text(str(port), encoding="utf-8")
    _write_state(True, pid=pid, port=port, urls=urls)


def _clear_runtime() -> None:
    _cleanup_stale_files()
    _write_state(False)


def _make_tray_icon():
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(124, 58, 237, 255))
    draw.ellipse((18, 18, 46, 46), fill=(167, 139, 250, 200))
    return img


def _run_tray(url: str) -> None:
    import pystray

    def on_open(icon, _item):
        webbrowser.open(url)

    def on_status(icon, _item):
        info = status()
        _log(f"Status: running={info['running']} url={info['url']} pid={info['pid']}")

    def on_quit(icon, _item):
        _log("Quit requested from tray")
        icon.stop()
        stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Open Persona Studio", on_open, default=True),
        pystray.MenuItem("Status", on_status),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("Persona Studio", _make_tray_icon(), "Persona Studio", menu)
    icon.run()


def _attach_stdio_to_log() -> None:
    """Detached Windows workers have no stdout — uvicorn/Gradio need a stream."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    stream = LOG_FILE.open("a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def run_server(*, no_tray: bool = False) -> None:
    """Run Gradio in this process (called by the background worker)."""
    import traceback

    _attach_stdio_to_log()
    os.environ["PERSONA_BACKGROUND"] = "1"
    _log(f"Worker boot pid={os.getpid()} exe={sys.executable} argv={sys.argv}")
    pid = os.getpid()

    def _shutdown(*_args):
        _clear_runtime()
        _log("Daemon exiting")

    atexit.register(_shutdown)

    try:
        from app import STUDIO_CSS, _gradio_theme, build_ui
        from remote import build_urls, gradio_launch_kwargs, save_urls

        port = pick_port(DEFAULT_PORT)
        _log(f"Server starting on port {port} (pid {pid})")

        demo = build_ui()
        launch_kwargs = gradio_launch_kwargs(quiet=True, background=True)
        launch_kwargs.update(
            server_port=port,
            show_error=False,
            inbrowser=False,
            theme=_gradio_theme(),
            css=STUDIO_CSS,
        )

        urls_holder: dict = {}

        def _blocking_launch() -> None:
            try:
                result = demo.launch(prevent_thread_lock=True, **launch_kwargs)
                if isinstance(result, tuple) and len(result) >= 3 and result[2]:
                    urls_holder["share"] = result[2]
                while True:
                    time.sleep(3600)
            except Exception:
                _log(f"Gradio server crashed: {traceback.format_exc()}")
                raise

        def _wait_for_server() -> None:
            for _ in range(60):
                if port_is_listening(port):
                    return
                time.sleep(0.5)
            _log(f"WARNING: port {port} not listening after 30s")

        def _finalize_startup():
            public, ptype = start_public_tunnel(port)
            share = urls_holder.get("share")
            if not public and share:
                public, ptype = share, "gradio"
            urls = build_urls(port, public=public, public_type=ptype)
            save_urls(urls)
            _save_runtime(pid, port, urls)
            return urls

        server_thread = threading.Thread(
            target=_blocking_launch,
            name="gradio-server",
            daemon=False,
        )
        server_thread.start()
        _wait_for_server()
        urls = _finalize_startup()

        if no_tray:
            _log(f"Server ready — local {urls.local} | LAN {urls.lan} | public {urls.public}")
            print_urls(urls)
        else:
            _log(f"Server ready — best link: {urls.best}")
            tray_thread = threading.Thread(
                target=_run_tray,
                args=(urls.best,),
                name="persona-tray",
                daemon=True,
            )
            tray_thread.start()

        while server_thread.is_alive():
            server_thread.join(timeout=30)
        _log("Gradio server thread exited — daemon shutting down")
    except Exception:
        _log(f"FATAL: {traceback.format_exc()}")
        _clear_runtime()
        raise


def _startup_command() -> str:
    return f'"{_silent_python()}" "{APP_DIR / "daemon.py"}" run'


def install_startup() -> str:
    if sys.platform != "win32":
        return "Auto-start install is Windows-only. Use your OS scheduler manually."

    ps = f"""
$TaskName = "{TASK_NAME}"
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {{ Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }}
$Action = New-ScheduledTaskAction -Execute "{_silent_python()}" -Argument '"{APP_DIR / "daemon.py"}" run' -WorkingDirectory "{APP_DIR}"
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Persona Studio background server" -RunLevel Limited | Out-Null
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if result.returncode != 0:
        return f"Install failed: {result.stderr or result.stdout}"

    _create_desktop_shortcut()
    start(open_browser=True)
    return (
        f"Installed auto-start at login (task: {TASK_NAME}). "
        f"A desktop shortcut was created. Look for the tray icon — open {get_url()} anytime."
    )


def uninstall_startup() -> str:
    stop()
    if sys.platform != "win32":
        return "Removed running instance."

    ps = f"""
$TaskName = "{TASK_NAME}"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    shortcut = _desktop_shortcut_path()
    if shortcut.exists():
        shortcut.unlink(missing_ok=True)
    return f"Removed auto-start task '{TASK_NAME}' and stopped the server."


def _desktop_shortcut_path() -> Path:
    desktop = Path.home() / "Desktop"
    return desktop / "Persona Studio.url"


def _create_desktop_shortcut() -> None:
    url = get_url()  # best URL: public > LAN > local
    shortcut = _desktop_shortcut_path()
    content = (
        "[InternetShortcut]\r\n"
        f"URL={url}\r\n"
        "IconIndex=0\r\n"
    )
    shortcut.write_text(content, encoding="utf-8")


def open_browser() -> str:
    info = status()
    if not info["running"]:
        result = start(open_browser=True)
        return result
    webbrowser.open(info["url"])
    return f"Opened {info['url']}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Persona Studio background daemon")
    parser.add_argument(
        "command",
        nargs="?",
        default="start",
        choices=["start", "stop", "status", "run", "install", "uninstall", "open"],
    )
    parser.add_argument("--no-tray", action="store_true", help="Run without system tray icon")
    parser.add_argument("--open", action="store_true", help="Open browser after start")
    args = parser.parse_args()

    if args.command == "start":
        print(start(no_tray=args.no_tray, open_browser=args.open))
    elif args.command == "stop":
        print(stop())
    elif args.command == "status":
        info = status()
        state = "running" if info["running"] else "stopped"
        print(f"Status: {state}")
        if info["pid"]:
            print(f"PID: {info['pid']}")
        print(f"URL: {info['url']}")
        print(f"Log: {info['log_file']}")
    elif args.command == "run":
        run_server(no_tray=args.no_tray)
    elif args.command == "install":
        print(install_startup())
    elif args.command == "uninstall":
        print(uninstall_startup())
    elif args.command == "open":
        print(open_browser())


if __name__ == "__main__":
    main()