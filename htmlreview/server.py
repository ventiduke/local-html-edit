"""Local web server: start page, edit-mode documents, and the JSON API."""

from __future__ import annotations

import mimetypes
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import picker, transform

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

    @property
    def folder(self) -> Path:
        return self.path.parent

    @property
    def url(self) -> str:
        return f"/doc/{self.token}/{quote(self.path.name)}"

    @property
    def working_copy(self) -> Path:
        return derived_path(self.path, "edit")


def derived_path(original: Path, kind: str) -> Path:
    """report.html -> report.<kind>.html"""
    return original.with_name(f"{original.stem}.{kind}{original.suffix}")


class OpenRequest(BaseModel):
    path: str


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
    pick: Callable[[str | None], str | None] = picker.pick_file,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    session = Session()
    api_token = api_token or secrets.token_urlsafe(24)
    app.state.session = session
    app.state.api_token = api_token

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Only our own origin: blocks DNS-rebinding attacks from other sites.
        if request.headers.get("host", "").lower() not in allowed_hosts:
            return Response("Bad host", status_code=403)
        if request.url.path.startswith("/api/"):
            # Other sites can't read our pages to learn the token, and can't
            # send a custom header cross-origin without a CORS preflight.
            if not secrets.compare_digest(request.headers.get("x-hr-token", ""), api_token):
                return JSONResponse({"error": "Missing or wrong API token."}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        return response

    # -- pages ------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def start_page():
        page = (STATIC_DIR / "start.html").read_text(encoding="utf-8")
        return HTMLResponse(page.replace("{{API_TOKEN}}", api_token), headers=_no_store)

    @app.get("/__hr/{name}")
    def editor_asset(name: str):
        path = STATIC_DIR / name
        if name not in ("editor.js", "editor.css") or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers=_no_store)

    @app.get("/doc/{token}/{rest:path}")
    def document(token: str, rest: str):
        doc = session.docs.get(token)
        if doc is None:
            return _not_open_page()
        if rest == doc.path.name:
            return _edit_page(doc)
        return _static_file(doc.folder, rest)

    def _edit_page(doc: Doc) -> HTMLResponse:
        source = doc.working_copy if doc.working_copy.is_file() else doc.path
        text, _ = transform.decode(source.read_bytes())
        tagged = transform.tag(text)
        config = {
            "apiToken": api_token,
            "doc": doc.token,
            "name": doc.path.name,
            "path": str(doc.path),
            "author": author,
            "resumed": source != doc.path,
            "editable": tagged.editable,
        }
        page = transform.inject(tagged.html, config, "/__hr/editor.js")
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

    @app.post("/api/pick")
    def api_pick():
        initial = str(session.last_folder) if session.last_folder else None
        try:
            chosen = pick(initial)
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

    return app


_no_store = {"Cache-Control": "no-store"}


def _static_file(folder: Path, rest: str) -> Response:
    root = folder.resolve()
    try:
        target = (root / rest).resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(404) from None
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target, headers=_no_store)


def _not_open_page() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html><meta charset="utf-8"><title>Not open</title>
<body style="font:16px system-ui,sans-serif;max-width:36em;margin:4em auto;padding:0 16px">
<h1>This file isn't open</h1>
<p>The review tool was probably restarted since this page was opened.</p>
<p><a href="/">Open it again from the start page</a></p></body>""",
        status_code=404,
    )
