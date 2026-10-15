# Local HTML Review & Edit Tool — Build Plan

Oct 15, 2026 (revised the same day after review; see "Decisions" at the end)

**Progress:** milestones 1–4 are built. From milestone 5, atomic writes, the on-disk change check, detached comments and the original-changed warning are done; editing elements with implicit end tags is still open.

## Goal and scope

Build a local tool that opens an existing HTML file in the browser, lets a person edit text in place and leave comments, then publishes a clean HTML file with every editing artifact removed.

**In scope**

- Runs locally with one command and opens in the browser
- Works on Windows and macOS (Linux is a bonus, not a target)
- Inline text editing of an existing HTML file
- Comments anchored to specific text elements or selected text, kept in a sidecar JSON file
- Save, resume later, and publish to clean HTML
- One long-running session: open the tool once, then open, edit, publish and open the next file without leaving it

**Out of scope for v1**

- Any built-in AI or agent integration. The tool must work fully without an agent. An agent can later read the comments file, edit the HTML, and mark comments resolved.
- Multi-user collaboration, auth, or hosting
- Layout or style editing (drag, resize, CSS changes); v1 is text plus comments
- Editing or commenting on charts. Charts render normally but are inert; comment on a chart's title or caption instead.

**Design principle:** the original HTML is never modified, and the comments file is the contract between the human reviewer and any later agent.

## User flow

1. **Start the tool.** Run `uv run htmlreview` (or `python -m htmlreview`), optionally with a file path. The server starts on `127.0.0.1` and opens a browser tab on the start page. The server keeps running until the terminal is closed or Ctrl+C is pressed.
2. **Pick a file.** The start page has an **Open HTML file…** button that shows the native Windows/macOS file dialog, plus a "paste a path" box as a fallback. Passing a path on the command line skips this step. No folder listing or recent-files list in v1.
3. **Edit mode.** The same tab switches to the report. The server reads the source (working copy if one exists, otherwise the original), adds `data-eid` IDs to editable text elements, injects the editor, and serves the result from the report's own folder so images, CSS, scripts and data files load exactly as when the file is opened directly. Nothing is written to disk yet.
4. **Edit and comment.**
   - Click a text element to edit it in place.
   - Select text (or click a text element) and press Comment to add a note in the sidebar.
   - The sidebar lists comments with status (open / resolved) and jumps to their anchor on click.
5. **Save.** Writes the working copy and the comments sidecar. Closing and reopening resumes from them.
6. **Publish.** After a confirmation that says how many open comments will be deleted, writes `report.published.html` next to the original, with no `data-eid`, injected script or editor markup. The working copy and comments sidecar are then deleted. The page then offers **Open another file**.
7. **Next file.** The toolbar's **Open…** button picks another file at any time; if there are unsaved changes the tool asks first.

## Architecture

One local Python process serves the editor to the browser and is the only part that reads or writes files.

```
+--------------------+        +----------------------------+        +------------------------+
| Browser (one tab)  |        | Local server (FastAPI)     |        | Files on disk          |
|                    |  HTTP  |                            | files  |                        |
| Start page         | <----> | /            start page    | <----> | report.html            |
|  Open… (native     |        | /doc/<t>/…   report+assets |        |  original (read-only)  |
|  dialog via server)|        | /api/pick    file dialog   |        | report.edit.html       |
|                    |        | /api/open    open by path  |        |  working copy          |
| Edit mode page     |        | /api/save, /api/publish    |        | report.comments.json   |
|  report + editor   |        |                            |        |  comments sidecar      |
|  toolbar/sidebar   |        | transform.py               |        | report.published.html  |
|  in Shadow DOM     |        |  tag eids, inject, splice  |        |  clean output          |
+--------------------+        +----------------------------+        +------------------------+
```

The browser never writes to disk directly; a later agent works only from the HTML and the comments sidecar, so it needs no access to the tool.

## Data formats

| File | Purpose | Lifetime |
| --- | --- | --- |
| `report.html` | Original; never modified by the tool | Permanent |
| `report.edit.html` | Working copy: the original plus `data-eid` attributes and saved text edits, no injected script | Written on every save (including comment-only saves), deleted on publish |
| `report.comments.json` | Comments sidecar; the agent interface | Written on save, deleted on publish |
| `report.published.html` | Clean final output | Written on publish; overwritten if published again |

Saving always writes the working copy, even when only comments changed, so a later agent edits `report.edit.html` (whose eids match the comments) and never the original.

### Element IDs (`data-eid`)

IDs are assigned in document order as `e1`, `e2`, … to *editable text elements*:

- Candidate tags: `p`, `h1`–`h6`, `li`, `td`, `th`, `dt`, `dd`, `caption`, `figcaption`, `blockquote`, `span`.
- An element is tagged only if **all** of these hold:
  1. Everything inside it is inline text markup (`a`, `b`, `strong`, `i`, `em`, `u`, `s`, `sub`, `sup`, `span`, `code`, `small`, `mark`, `abbr`, `time`, `br`, …). So a `<p>` with a `<span>` inside is tagged as one unit; a `<blockquote>` containing `<p>`s is not, but its `<p>`s are; an `<li>` with a nested list is not, but the inner `<li>`s are.
  2. No ancestor is already tagged (outermost wins; a `<span>` inside a tagged `<p>` is never tagged separately).
  3. It is not inside `head`, `script`, `style`, `template`, `noscript`, `svg`, `math`, `canvas`, `textarea`, `select`, `button`, `object`, `iframe`.
  4. It has an explicit, correctly matched end tag in the source (elements relying on implicit end tags are left alone).
  5. It contains non-whitespace text in the source. Empty containers that JavaScript fills in are therefore never tagged.
- Charts and chart containers are never tagged.
- IDs are persisted in the working copy, so they stay stable across sessions. When the working copy gains new candidate elements (for example an agent added a paragraph), they get the next free number after the current maximum.

### JS-filled text guard

Some text exists in the source but is overwritten by JavaScript at load time (a KPI number, a "last updated" date). The server sends the editor the source text of every tagged element; after the page has finished loading, the editor compares it with the live text and makes any element that differs **comment-only**. A JS-generated value is therefore never saved back into the file.

### Comments sidecar schema

```json
{
  "version": 1,
  "source": "report.html",
  "source_sha256": "9f2c…",
  "comments": [
    {
      "id": "c7",
      "anchor": {
        "eid": "e42",
        "quote": "win rate rose 4%",
        "prefix": "In EMEA, ",
        "suffix": " week over week"
      },
      "body": "Is this significant? Add a CI.",
      "status": "open",
      "author": "alex",
      "created": "2026-10-15T10:10:00-07:00",
      "replies": [
        { "author": "sam", "body": "Yes, p < 0.05.", "created": "2026-10-15T11:02:00-07:00" }
      ],
      "resolution": null
    }
  ]
}
```

- `anchor.eid` is the primary locator; `quote` + `prefix`/`suffix` let the tool or an agent re-find the spot if the element was rewritten.
- Element-level comments (no text selection) omit `quote`, `prefix`, `suffix`.
- `status` is `open` or `resolved`. An agent that acts on a comment sets `resolved` and fills `resolution` with a one-line note of what changed.
- New comment IDs are `c<max+1>`.
- `author` defaults to the OS username; `--author NAME` overrides it.
- `source_sha256` is the hash of the original file, used to warn when it changed outside the tool.
- The schema also lives in `schema/comments.schema.json`, and the README documents it for agents.

## Components and API

**Repo layout**

```
pyproject.toml
README.md
docs/PLAN.md
schema/comments.schema.json
htmlreview/
  __main__.py      # CLI: python -m htmlreview [path] [--port] [--no-browser] [--author]
  server.py        # FastAPI app, routes below
  transform.py     # tokenizer, tag eids, inject editor, apply edits, strip for publish
  comments.py      # load/save/validate sidecar JSON
  picker.py        # native file dialog, run as a short-lived subprocess
  static/
    editor.js      # toolbar, contenteditable, selection -> comment, sidebar, save
    editor.css     # styles loaded inside the editor's Shadow DOM
    start.html     # Open HTML file… page
examples/          # fake chart-heavy reports for testing
tests/
```

**Stack:** Python 3.11+, managed with `uv`; FastAPI + uvicorn; the standard library `html.parser` for source positions (no re-serialization, so no BeautifulSoup); vanilla JS for the editor (no build step).

**Endpoints**

| Method | Path | Does |
| --- | --- | --- |
| GET | `/` | Start page with **Open HTML file…** and a paste-a-path box |
| POST | `/api/pick` | Shows the native file dialog; opens the chosen file; returns its edit URL |
| POST | `/api/open` | Body `{path}`; opens that file; returns its edit URL |
| GET | `/doc/<token>/<name>.html` | The picked file in edit mode (eids + editor injected) |
| GET | `/doc/<token>/<anything else>` | Static passthrough for files under the picked file's folder |
| GET | `/api/comments?doc=…` | Return the sidecar JSON |
| POST | `/api/save?doc=…` | Body `{edits: {eid: innerHTML}, comments: [...]}`; writes working copy + sidecar |
| POST | `/api/publish?doc=…` | Writes `report.published.html`, then deletes the working copy and sidecar |

`<token>` is a short random ID the server maps to the picked file's folder, so the report is served from a URL that behaves like its own folder (relative links work, no `<base>` needed) and absolute paths never appear in URLs. Tokens last for the life of the server process.

**editor.js responsibilities**

- Toolbar and sidebar rendered inside a Shadow DOM so page CSS and editor CSS can't affect each other
- Toolbar: file name, Open…, Save, Publish, Comment, sidebar toggle
- Toggle `contenteditable` on editable `data-eid` elements; track dirty eids; JS-filled text guard
- Sanitize on save: strip stray `div`/`br`, `&nbsp;`, inline styles the browser adds; paste as plain text
- Comment creation from a text selection (eid, quote, prefix, suffix) or an element click
- Sidebar listing comments; click to scroll to and highlight the anchor; resolve and reply
- Highlights drawn as overlays, never written into the page source
- Ctrl/Cmd+S saves; warn on unsaved changes when leaving or opening another file

## Risks and design rules

| Risk | Rule |
| --- | --- |
| Chart-heavy pages: JS-rendered content gets serialized into the file | Never save the live DOM. Send only `{eid: innerHTML}` for edited elements; the server splices them into the source. Charts are never tagged. JS-filled text guard makes overwritten elements comment-only. |
| Large embedded data altered by parsing and re-serializing | Never re-serialize. A tokenizer on `html.parser` records source offsets; tagging, editing and stripping are text splices. Script, style and data blocks are never touched. Tested: tag-then-strip returns the original bytes exactly. |
| Encodings and line endings (Windows) | Read bytes, detect charset (BOM, `<meta charset>`, default UTF-8), decode losslessly, write back with the same encoding. CRLF is preserved. |
| `contenteditable` produces messy markup | Edit only elements containing inline text. Sanitize before sending. Paste as plain text. |
| Editor artifacts leak into the published file | The injected script is never written to disk. Publish removes `data-eid` from the server-side source. Test that publish output contains none of them. |
| Broken relative paths (images, CSS, data files) | Serve the report from `/doc/<token>/`, which maps to its own folder. |
| Data loss | The original is never written to. Working copy, sidecar and published file are written atomically (temp file + rename). Publish asks for confirmation. |
| Comment anchors drift after edits | Keep `eid` + `quote` + `prefix`/`suffix`; if the quote is no longer found, show the comment as "detached" rather than dropping it. |
| Working copy edited outside the tool (e.g. by an agent) while open | Save checks the working copy's modification time and offers to reload instead of overwriting. |
| Original edited outside the tool | Store the original's hash in the sidecar; warn on mismatch at load. |
| Other websites in the browser poking the local server | Bind to `127.0.0.1` only. Reject requests whose `Host` isn't `127.0.0.1`/`localhost` (blocks DNS rebinding). Every `/api/` call needs a random per-run token that only the tool's own pages carry, sent in a custom header (blocks cross-site form posts). Serve only files the user picked and files under their folders. |
| Native file dialog | macOS requires Tk on the main thread and the server owns it, so the dialog runs in a short-lived subprocess that prints the chosen path. It is forced to the front (Windows tends to open it behind the browser). If Tk is unavailable, the paste-a-path box still works. |

## Milestones

Build in five milestones, each usable on its own; stop and test after each.

1. **Serve and inject.** Start page with native Open dialog and paste-a-path; CLI path; serve from `/doc/<token>/` with eids and the toolbar; assets and charts load; security checks; fake example reports; tokenizer + tagging tests.
   - Done when: the example reports render identically to opening them directly, plus the toolbar, and tag-then-strip is byte-identical.
2. **Edit and save.** Inline editing of tagged elements; JS-filled text guard; save writes the working copy (never the original); reload resumes edits; Open… with unsaved-changes warning.
   - Done when: edit three paragraphs and a table cell, save, restart the server, and the edits are still there with no stray markup. Saving a chart-heavy report with no edits leaves its scripts and embedded data byte-identical.
3. **Comments.** Create from selection or element, sidebar list, jump-to, resolve, reply, persisted to the sidecar.
   - Done when: the JSON matches the schema and anchors re-find their text after a reload.
4. **Publish.** Confirmation, write `report.published.html`, leave the original untouched, delete the working copy and sidecar, offer Open another file.
   - Done when: a diff of published vs original shows only the intended text edits, and no `data-eid` or editor script remains.
5. **Hardening.** Detached comments, hash mismatch warning, working-copy modification check, atomic writes, more tests for transform and publish.

**Later (not v1):** a "copy agent prompt" button that produces instructions telling an agent to read the sidecar, apply changes and mark comments resolved; style edits; image swaps.

## Decisions

Settled on 2026-10-15.

- **Content:** reports are chart-heavy with lots of embedded data, from any chart library. Save never serializes the live DOM, script and data blocks are never touched.
- **Charts:** not editable and not commentable. Comment on the title or caption.
- **Platforms:** Windows and macOS. Primary testing on Windows.
- **Tooling:** `uv`, `pytest`.
- **Publish:** keep the original untouched, write `report.published.html` next to it, then delete everything else the session created.
- **Comments on publish:** deleted, not archived, after a confirmation.
- **Opening files:** native OS file dialog or a pasted path, from the start page or the toolbar at any time, or a CLI path. No folder listing or recent-files list.
- **One session, many files:** the tool stays open; files are opened, edited and published one after another in the same tab.
- **Positions over parsing:** the standard library tokenizer with source offsets, not BeautifulSoup, so untouched bytes stay untouched.
- **Test data:** fake example reports in `examples/`.

## Notes for implementation

- Keep the editor framework-free (vanilla JS) so it can be injected into any page; UI lives in a Shadow DOM; any page-level globals, attributes and styles are namespaced `__hr` / `data-hr-*`.
- Treat `report.comments.json` as a public contract: document the schema in the README so an agent can use it without reading the code.
- Tests for `transform.py`: tagging is deterministic, applying edits touches only the targeted eids, tag-then-strip is byte-identical, and publish strips every artifact.
