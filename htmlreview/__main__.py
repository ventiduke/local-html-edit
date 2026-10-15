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

from .server import OpenError, create_app

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
    args = parser.parse_args(argv)

    try:
        author = args.author or getpass.getuser()
    except Exception:
        author = "reviewer"

    sock = _bind(args.port)
    port = sock.getsockname()[1]
    app = create_app(
        allowed_hosts={f"127.0.0.1:{port}", f"localhost:{port}"},
        author=author,
    )

    start_url = f"http://127.0.0.1:{port}/"
    if args.path:
        try:
            doc = app.state.session.open(args.path)
        except OpenError as exc:
            sys.exit(str(exc))
        start_url = f"http://127.0.0.1:{port}{doc.url}"

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))

    def announce():
        while not server.started:
            if server.should_exit:
                return
            time.sleep(0.05)
        print(f"HTML Review is running at {start_url}")
        print("Keep this window open while you work. Press Ctrl+C to quit.")
        if not args.no_browser:
            webbrowser.open(start_url)

    threading.Thread(target=announce, daemon=True).start()
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
