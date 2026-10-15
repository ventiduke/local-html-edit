"""The "Agent prompt": a short request that points the agent at the editable-html skill."""

from __future__ import annotations

import shutil
from pathlib import Path

SKILL_NAME = "editable-html"
SKILL_FILE = Path(__file__).parent / "skills" / SKILL_NAME / "SKILL.md"


def installed_skill_path() -> Path:
    return Path.home() / ".claude" / "skills" / SKILL_NAME / "SKILL.md"


def install_skill() -> Path:
    """Copy the skill to ~/.claude/skills so Claude Code finds it anywhere."""
    target = installed_skill_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SKILL_FILE, target)
    return target


def _clip(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe_comment(comment: dict, element_text: dict[str, str]) -> str:
    anchor = comment["anchor"]
    eid = anchor["eid"]
    if anchor.get("quote"):
        where = f'on "{_clip(anchor["quote"], 120)}" (element {eid})'
    elif element_text.get(eid):
        where = f'on element {eid} ("{_clip(element_text[eid], 80)}")'
    else:
        where = f"on element {eid}"
    lines = [f"- {comment['id']}, {where}: {comment['body'].strip()}"]
    for reply in comment.get("replies") or []:
        lines.append(f"  Reply from {reply.get('author') or 'someone'}: {reply['body'].strip()}")
    return "\n".join(lines)


def agent_prompt(
    working_copy: Path, sidecar: Path, open_comments: list[dict], element_text: dict[str, str]
) -> str:
    listed = "\n".join(describe_comment(c, element_text) for c in open_comments)
    return f"""\
In this file: {working_copy}
address these review comments, using the {SKILL_NAME} skill (if you don't have it, read {SKILL_FILE}).
Record what you did in {sidecar}.

{listed}
"""
