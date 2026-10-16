"""Local web server: start page, edit-mode documents, and the JSON API."""

from __future__ import annotations

import mimetypes
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import comments, picker, transform
from .prompt import agent_prompt, installed_skill_path

STATIC_DIR = Path(__file__).parent / "static"
HTML_SUFFIXES = (".html", ".htm")

# Windows takes MIME types from the registry, which is sometimes wrong (.js as
# text/plain breaks module scripts). Pin the ones reports depend on.
for _type, _ext in [
    ("text/javascript", ".js"),
    ("text/javascript", ".mjs"),
    ("text/css", ".css"),
    ("image/svg+xml", ".svg"),
    ("application/json", ".json"),
    ("application/wasm", ".wasm"),
    ("text/html", ".html"),
    ("text/html", ".htm"),
]:
    mimetypes.add_type(_type, _ext)


@dataclass
class Doc:
    token: str
    path: Path  # the original file
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def folder(self) -> Path:
        return self.path.parent

    @property
    def url(self) -> str:
        return f"/doc/{self.token}/{quote(self.path.name)}"

    @property
    def working_copy(self) -> Path:
        return derived_path(self.path, "edit")

    @property
    def published(self) -> Path:
        return derived_path(self.path, "published")

    @property
    def sidecar(self) -> Path:
        return self.path.with_name(f"{self.path.stem}.comments.json")

    def source(self) -> Path:
        """Where editing resumes from: the working copy if there is one."""
        return self.working_copy if self.working_copy.is_file() else self.path

    def version(self) -> str:
        """Fingerprint of the files a save would overwrite.

        If it changes between loading the page and saving (an agent edited
        the working copy or resolved comments), the save is refused.
        """
        sidecar = self.sidecar.read_bytes() if self.sidecar.is_file() else b""
        return comments.sha256(self.source().read_bytes() + b"\0" + sidecar)


def derived_path(original: Path, kind: str) -> Path:
    """report.html -> report.<kind>.html"""
    return original.with_name(f"{original.stem}.{kind}{original.suffix}")


class OpenRequest(BaseModel):
    path: str


class PresenceRequest(BaseModel):
    id: str
    state: str  # hello | beat | bye


class SaveRequest(BaseModel):
    version: str
    edits: dict[str, str] = {}
    comments: list = []


class Session:
    """Files opened during this run of the server."""

    def __init__(self) -> None:
        self.docs: dict[str, Doc] = {}
        self._by_path: dict[str, str] = {}
        self.last_folder: Path | None = None

    def open(self, raw_path: str) -> Doc:
        path = resolve_user_path(raw_path)
        key = str(path).casefold()
        token = self._by_path.get(key)
        if token is None:
            token = secrets.token_urlsafe(6)
            self.docs[token] = Doc(token, path)
            self._by_path[key] = token
        self.last_folder = path.parent
        return self.docs[token]


class OpenError(ValueError):
    pass


class Presence:
    """Tracks open browser tabs so the server can exit when the last one closes.

    Every page the tool serves says hello when it loads, checks in while open,
    and says goodbye when it unloads. Only a goodbye counts as closing a tab:
    browsers pause or freeze background tabs (macOS App Nap, Chrome Memory
    Saver, Edge sleeping tabs), so a tab that has gone quiet is usually still
    open. A goodbye is often followed by the next page's hello (reload,
    opening another file, VS Code rebuilding its browser view), so the server
    waits ``grace`` seconds with no tabs and no requests before exiting.
    Tabs that never say goodbye (a crashed browser) are given up on after
    ``orphan`` seconds.
    """

    def __init__(self, grace: float = 60.0, orphan: float = 12 * 3600.0, clock=time.monotonic):
        self.grace = grace
        self.orphan = orphan
        self.clock = clock
        self.clients: dict[str, float] = {}
        self.gone: set[str] = set()  # tabs that said goodbye
        self.last_request = clock()
        self.ever_seen = False
        self._lock = threading.Lock()

    def request(self) -> None:
        self.last_request = self.clock()

    def update(self, client: str, state: str) -> None:
        with self._lock:
            if state == "bye":
                self.clients.pop(client, None)
                self.gone.add(client)
                return
            if state != "hello" and client in self.gone:
                return  # a check-in that was already on its way when the tab closed
            self.gone.discard(client)
            self.clients[client] = self.clock()
            self.ever_seen = True

    def resume(self) -> None:
        """After the computer slept, give open tabs time to check in again."""
        with self._lock:
            now = self.clock()
            self.last_request = now
            self.clients = dict.fromkeys(self.clients, now)

    def idle(self) -> str | None:
        """Why the server should stop now, or None to keep running."""
        with self._lock:
            now = self.clock()
            orphaned = [c for c, t in self.clients.items() if now - t >= self.orphan]
            for c in orphaned:
                del self.clients[c]
            if not self.ever_seen or self.clients or now - self.last_request <= self.grace:
                return None
            if orphaned:
                hours = round(self.orphan / 3600)
                return f"No HTML Review tab had checked in for {hours} hours, so it stopped."
            return "All HTML Review tabs were closed, so it stopped."


def resolve_user_path(raw: str) -> Path:
    # Windows "Copy as path" wraps the path in quotes.
    cleaned = raw.strip().strip('"').strip("'").strip()
    if not cleaned:
        raise OpenError("No path given.")
    path = Path(cleaned).expanduser()
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise OpenError(f"File not found: {cleaned}") from None
    if not path.is_file():
        raise OpenError(f"Not a file: {path}")
    if path.suffix.lower() not in HTML_SUFFIXES:
        raise OpenError(f"Not an HTML file: {path.name}")
    # Opening a working copy means resuming its original.
    stem = path.stem
    if stem.lower().endswith(".edit"):
        original = path.with_name(stem[: -len(".edit")] + path.suffix)
        if not original.is_file():
            raise OpenError(
                f"{path.name} is a working copy but its original {original.name} is missing."
            )
        path = original
    return path


def create_app(
    *,
    allowed_hosts: set[str],
    api_token: str | None = None,
    author: str = "",
    pick: Callable[[str | None], str | None] | None = None,
    cancel_pick: Callable[[], bool] | None = None,
    on_exit: Callable[[], None] = lambda: None,
    presence: Presence | None = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    session = Session()
    if pick is None:
        dialog = picker.Picker()
        pick, cancel_pick = dialog.pick, dialog.cancel
    cancel_pick = cancel_pick or (lambda: False)
    api_token = api_token or secrets.token_urlsafe(24)
    presence = presence or Presence()
    app.state.session = session
    app.state.api_token = api_token
    app.state.presence = presence
    keepalive = f'<script src="/__hr/keepalive.js" data-hr data-hr-token="{api_token}"></script>'

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Only our own origin: blocks DNS-rebinding attacks from other sites.
        if request.headers.get("host", "").lower() not in allowed_hosts:
            return Response("Bad host", status_code=403)
        presence.request()
        if request.url.path.startswith("/api/"):
            # Other sites can't read our pages to learn the token, and can't
            # send a custom header cross-origin without a CORS preflight.
            if not secrets.compare_digest(request.headers.get("x-hr-token", ""), api_token):
                return JSONResponse({"error": "Missing or wrong API token."}, status_code=403)
            # Any call from a tab (saves, the change check) also says it's open.
            client = request.headers.get("x-hr-client")
            if client and request.url.path != "/api/presence":
                presence.update(client, "beat")
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        return response

    # -- pages ------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def start_page():
        page = (STATIC_DIR / "start.html").read_text(encoding="utf-8")
        page = page.replace("{{API_TOKEN}}", api_token).replace("{{KEEPALIVE}}", keepalive)
        return HTMLResponse(page, headers=_no_store)

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.get("/__hr/{name}")
    def editor_asset(name: str):
        path = STATIC_DIR / name
        if name not in ("editor.js", "editor.css", "keepalive.js") or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers=_no_store)

    @app.get("/doc/{token}/{rest:path}")
    def document(token: str, rest: str):
        doc = session.docs.get(token)
        if doc is None:
            return _not_open_page()
        if rest == doc.path.name:
            return _edit_page(doc)
        target = _static_target(doc.folder, rest)
        if target.suffix.lower() in HTML_SUFFIXES:
            # Other pages in the report's folder (the published file, linked
            # pages) get only the keepalive, so the tool stays running.
            text, _ = transform.decode(target.read_bytes())
            return HTMLResponse(transform.inject_snippet(text, keepalive), headers=_no_store)
        return FileResponse(target, headers=_no_store)

    def _edit_page(doc: Doc) -> HTMLResponse:
        with doc.lock:
            source = doc.source()
            version = doc.version()
            text, _ = transform.decode(source.read_bytes())
            comments_error = None
            try:
                sidecar = comments.load(doc.sidecar)
            except comments.CommentError as exc:
                sidecar, comments_error = None, str(exc)
            original_changed = bool(
                sidecar
                and sidecar.get("source_sha256")
                and sidecar["source_sha256"] != comments.sha256(doc.path.read_bytes())
            )
        tagged = transform.tag(text)
        config = {
            "apiToken": api_token,
            "doc": doc.token,
            "name": doc.path.name,
            "path": str(doc.path),
            "publishedName": doc.published.name,
            "author": author,
            "resumed": source != doc.path,
            "version": version,
            "editable": tagged.editable,
            "comments": sidecar["comments"] if sidecar else [],
            "commentsError": comments_error,
            "originalChanged": original_changed,
        }
        page = transform.inject(tagged.html, config, "/__hr/editor.js", extra=keepalive)
        # Sent as UTF-8; the HTTP header overrides any <meta charset>.
        return HTMLResponse(page, headers=_no_store)

    # -- API --------------------------------------------------------------

    @app.post("/api/open")
    def api_open(body: OpenRequest):
        try:
            doc = session.open(body.path)
        except OpenError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"url": doc.url, "name": doc.path.name}

    @app.post("/api/presence")
    def api_presence(body: PresenceRequest):
        presence.update(body.id, body.state)
        return {"ok": True}

    @app.post("/api/shutdown")
    def api_shutdown():
        on_exit()
        return {"ok": True}

    @app.post("/api/pick-cancel")
    def api_pick_cancel():
        return {"closed": cancel_pick()}

    @app.post("/api/pick")
    def api_pick():
        initial = str(session.last_folder) if session.last_folder else None
        try:
            chosen = pick(initial)
        except picker.PickerBusy as exc:
            return JSONResponse({"error": str(exc), "busy": True}, status_code=409)
        except picker.DialogUnavailable:
            return JSONResponse(
                {"error": "No file dialog is available here. Paste the file's path instead.",
                 "noDialog": True},
                status_code=501,
            )
        except Exception as exc:
            return JSONResponse({"error": f"File dialog failed: {exc}"}, status_code=500)
        if not chosen:
            return {"cancelled": True}
        try:
            doc = session.open(chosen)
        except OpenError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"url": doc.url, "name": doc.path.name}

    def _doc_or_404(token: str) -> Doc:
        doc = session.docs.get(token)
        if doc is None:
            raise HTTPException(404, "This file isn't open. Reopen it from the start page.")
        return doc

    def _render(doc: Doc, body: SaveRequest) -> tuple[str, str]:
        """Current source with eids and the requested edits applied."""
        text, enc = transform.decode(doc.source().read_bytes())
        return transform.apply_edits(transform.tag(text).html, body.edits), enc

    def _conflict() -> JSONResponse:
        return JSONResponse(
            {"error": "The working copy or comments changed on disk since this page was loaded.",
             "conflict": True},
            status_code=409,
        )

    @app.post("/api/save")
    def api_save(doc: str, body: SaveRequest):
        d = _doc_or_404(doc)
        with d.lock:
            if body.version != d.version():
                return _conflict()
            try:
                comments.load(d.sidecar)  # refuse to overwrite a sidecar we can't read
                html_text, enc = _render(d, body)
                sidecar = comments.build(
                    d.path.name, comments.sha256(d.path.read_bytes()), body.comments
                )
            except (transform.EditError, comments.CommentError) as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            comments.atomic_write(d.working_copy, transform.encode(html_text, enc))
            comments.atomic_write(d.sidecar, comments.dumps(sidecar))
            return {"version": d.version()}

    @app.post("/api/status")
    def api_status(doc: str):
        """Cheap poll: has anything on disk changed since the page loaded?"""
        d = _doc_or_404(doc)
        with d.lock:
            return {"version": d.version()}

    @app.post("/api/agent-prompt")
    def api_agent_prompt(doc: str):
        d = _doc_or_404(doc)
        with d.lock:
            try:
                sidecar = comments.load(d.sidecar)
            except comments.CommentError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            if sidecar is None or not d.working_copy.is_file():
                return JSONResponse(
                    {"error": "Save first: the agent works from the saved files."}, status_code=409
                )
            text, _ = transform.decode(d.working_copy.read_bytes())
        open_comments = [c for c in sidecar["comments"] if c["status"] == "open"]
        element_text = transform.tag(text).editable
        return {
            "open": len(open_comments),
            "prompt": agent_prompt(d.working_copy, d.sidecar, open_comments, element_text),
            "skillInstalled": installed_skill_path().is_file(),
        }

    @app.post("/api/publish")
    def api_publish(doc: str, body: SaveRequest):
        d = _doc_or_404(doc)
        with d.lock:
            if body.version != d.version():
                return _conflict()
            try:
                html_text, enc = _render(d, body)
            except transform.EditError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            clean = transform.strip_eids(html_text)
            comments.atomic_write(d.published, transform.encode(clean, enc))
            for leftover in (d.working_copy, d.sidecar):
                leftover.unlink(missing_ok=True)
        return {
            "published": d.published.name,
            "url": f"/doc/{d.token}/{quote(d.published.name)}",
        }

    return app


_no_store = {"Cache-Control": "no-store"}


def _static_target(folder: Path, rest: str) -> Path:
    """A file under ``folder``; 404 for anything missing or outside it."""
    root = folder.resolve()
    try:
        target = (root / rest).resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(404) from None
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404)
    return target


def _not_open_page() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html><meta charset="utf-8"><title>Not open</title>
<body style="font:16px system-ui,sans-serif;max-width:36em;margin:4em auto;padding:0 16px">
<h1>This file isn't open</h1>
<p>The review tool was probably restarted since this page was opened.</p>
<p><a href="/">Open it again from the start page</a></p></body>""",
        status_code=404,
    )
