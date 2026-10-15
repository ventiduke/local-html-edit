"""The comments sidecar (``report.comments.json``): load, validate, save.

This file is the public contract with agents; see README.md and
schema/comments.schema.json. Unknown keys are preserved so an agent can add
its own fields without the tool dropping them.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

VERSION = 1
STATUSES = ("open", "resolved")


class CommentError(ValueError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file in the same folder, then rename over the target."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(path: Path) -> dict | None:
    """The sidecar's contents, or None if there is none."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise CommentError(f"{path.name} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise CommentError(f"{path.name} should contain a JSON object.")
    data["comments"] = validate(data.get("comments", []))
    return data


def build(source_name: str, source_sha256: str, comments: list) -> dict:
    return {
        "version": VERSION,
        "source": source_name,
        "source_sha256": source_sha256,
        "comments": validate(comments),
    }


def dumps(doc: dict) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def validate(comments) -> list[dict]:
    if not isinstance(comments, list):
        raise CommentError("comments must be a list")
    seen: set[str] = set()
    out = []
    for i, c in enumerate(comments):
        where = f"comment {i + 1}"
        if not isinstance(c, dict):
            raise CommentError(f"{where} must be an object")
        cid = _str(c, "id", where)
        if cid in seen:
            raise CommentError(f"duplicate comment id {cid}")
        seen.add(cid)
        anchor = c.get("anchor")
        if not isinstance(anchor, dict):
            raise CommentError(f"{where}: anchor must be an object")
        _str(anchor, "eid", where)
        for key in ("quote", "prefix", "suffix"):
            if key in anchor and not isinstance(anchor[key], str):
                raise CommentError(f"{where}: anchor.{key} must be a string")
        _str(c, "body", where)
        status = c.setdefault("status", "open")
        if status not in STATUSES:
            raise CommentError(f"{where}: status must be 'open' or 'resolved'")
        _str(c, "author", where, required=False)
        _str(c, "created", where, required=False)
        replies = c.setdefault("replies", [])
        if not isinstance(replies, list):
            raise CommentError(f"{where}: replies must be a list")
        for r in replies:
            if not isinstance(r, dict):
                raise CommentError(f"{where}: each reply must be an object")
            _str(r, "body", where)
            _str(r, "author", where, required=False)
            _str(r, "created", where, required=False)
        c.setdefault("resolution", None)
        if c["resolution"] is not None and not isinstance(c["resolution"], str):
            raise CommentError(f"{where}: resolution must be a string or null")
        out.append(c)
    return out


def _str(obj: dict, key: str, where: str, required: bool = True) -> str | None:
    value = obj.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value):
        raise CommentError(f"{where}: {key} must be a non-empty string")
    return value
