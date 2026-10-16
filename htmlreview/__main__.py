"""Command line entry point: ``htmlreview [path]`` or ``python -m htmlreview [path]``."""

from __future__ import annotations

import argparse
import getpass
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from .prompt import SKILL_NAME, install_skill
from .server import OpenError, Presence, create_app

DEFAULT_PORT = 8765


def _bind(port: int) -> socket.socket:
    """Bind to 127.0.0.1:port, or any free port if that one is taken."""
    for candidate in (port, 0):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32":
            # Never share the port with another running server.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            # Allow an immediate restart on the same port (lingering TIME_WAIT).
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", candidate))
            return sock
        except OSError:
            sock.close()
    raise SystemExit("Could not bind a local port.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="htmlreview",
        description="Review, edit and comment on an HTML file, then publish clean HTML.",
    )
    parser.add_argument("path", nargs="?", help="HTML file to open right away")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    parser.add_argument("--author", help="name recorded on comments (default: your OS username)")
    parser.add_argument(
        "--install-skill",
        action="store_true",
        help="install the editable-html skill for Claude Code (~/.claude/skills) and exit",
    )
    args = parser.parse_args(argv)

    if args.install_skill:
        print(f"Installed the {SKILL_NAME} skill at {install_skill()}")
        return

    try:
        author = args.author or getpass.getuser()
    except Exception:
        author = "reviewer"

    sock = _bind(args.port)
    port = sock.getsockname()[1]
    presence = Presence()
    stop_reason: list[str] = []

    def request_exit(reason: str) -> None:
        if not stop_reason:
            stop_reason.append(reason)
        server.should_exit = True

    app = create_app(
        allowed_hosts={f"127.0.0.1:{port}", f"localhost:{port}"},
        author=author,
        on_exit=lambda: request_exit("Stopped from the browser."),
        presence=presence,
    )

    start_url = f"http://127.0.0.1:{port}/"
    if args.path:
        try:
            doc = app.state.session.open(args.path)
        except OpenError as exc:
            sys.exit(str(exc))
        start_url = f"http://127.0.0.1:{port}{doc.url}"

    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=3)
    )

    def announce():
        while not server.started:
            if server.should_exit:
                return
            time.sleep(0.05)
        print(f"HTML Review is running at {start_url}")
        print("It stops by itself about a minute after you close its browser tabs,")
        print("or use the Exit button, or press Ctrl+C here.")
        if not args.no_browser:
            webbrowser.open(start_url)

    def watchdog():
        last_tick = time.monotonic()
        while not server.should_exit:
            time.sleep(2)
            now = time.monotonic()
            if now - last_tick > 30:
                # The computer was asleep; tabs haven't had a chance to check in.
                presence.resume()
            last_tick = now
            reason = presence.idle()
            if reason:
                request_exit(reason)

    threading.Thread(target=announce, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    server.run(sockets=[sock])
    print(stop_reason[0] if stop_reason else "HTML Review stopped.")


if __name__ == "__main__":
    main()
