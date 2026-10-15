"""The "Agent prompt": instructions for an agent to address review comments."""

from __future__ import annotations

from pathlib import Path


def agent_prompt(original: Path, working_copy: Path, sidecar: Path, open_count: int) -> str:
    plural = "comment" if open_count == 1 else "comments"
    return f"""\
Please address the {open_count} open review {plural} on an HTML report.

Files:
- Edit this working copy: {working_copy}
- Review comments: {sidecar}
- Do not modify the original: {original}

For each comment in {sidecar.name} whose "status" is "open":
1. Find the element it is about in {working_copy.name}: "anchor.eid" matches that element's data-eid attribute. If "anchor.quote" is present, the comment is about that exact text ("anchor.prefix" and "anchor.suffix" are the text around it). Without a quote, the comment is about the whole element.
2. Read the comment's "body" and any "replies", and make the requested change in {working_copy.name}.
3. In {sidecar.name}, set the comment's "status" to "resolved" and "resolution" to a one-line summary of what you changed.

If a comment is unclear, or you decide not to change anything, leave its "status" as "open" and append a reply to its "replies" list explaining why: {{"author": "agent", "body": "...", "created": "<current time, ISO 8601 with UTC offset>"}}.

Rules:
- Keep every existing data-eid attribute exactly as it is. New elements you add don't need one.
- Only change what the comments ask for. Don't reformat or reindent the file, and don't touch <script> or <style> blocks or embedded data unless a comment asks for it.
- Keep {sidecar.name} valid JSON with the same structure. Don't remove comments or change their ids.
- The reviewer has the file open in a review tool that reloads automatically when you save, so write each file in one go.
"""
