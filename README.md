# HTML Review

A local tool for reviewing HTML reports: open a file in your browser, edit text in place, leave comments, then publish a clean copy. The original file is never modified.

See [docs/PLAN.md](docs/PLAN.md) for the full design.

## Status

| Milestone | State |
| --- | --- |
| 1. Serve and inject (open files, toolbar, editable-element detection) | Done |
| 2. Edit and save | Done |
| 3. Comments | Done |
| 4. Publish | Done |
| 5. Hardening | Partly done (atomic writes, on-disk change detection, detached comments, original-changed warning) |

## Running it

You need [uv](https://docs.astral.sh/uv/). It installs a suitable Python for you if you don't have one.

- **Windows (PowerShell):** `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **macOS:** `curl -LsSf https://astral.sh/uv/install.sh | sh`

Then, from this folder:

```
uv run htmlreview
```

A browser tab opens on the start page. Click **Open HTML file…** (or paste a path) to open a report. Use the toolbar's **Open…** button to switch to another file at any time.

**Stopping it.** Click **Exit** in the toolbar (or on the start page), or just close the browser tab: the tool notices when none of its tabs are open any more and stops by itself about 15 seconds later, so it never lingers in the background. Reloading or switching files doesn't count as closing. Ctrl+C in the terminal also works. If you have unsaved changes, Exit asks first.

Options:

```
uv run htmlreview path\to\report.html   # open a file straight away
uv run htmlreview --port 9000           # default port is 8765 (a free one is used if it's taken)
uv run htmlreview --no-browser          # don't open a browser tab
uv run htmlreview --author "Alex"       # name recorded on comments (default: your OS username)
```

## Using it

**Edit text.** Hover to see what's editable (blue dashed outline), then click and type. Press Enter or Esc, or click elsewhere, to finish. Edited elements get a blue bar on the left until saved. Pasting always pastes plain text. Inside editable text, a plain click edits; Ctrl+click (Cmd+click on a Mac) follows a link.

**Script-filled text.** Text the page's own JavaScript fills in, like the KPI numbers in the sales example, is outlined in orange. You can comment on it but not edit it, so a script-generated value is never saved into the file.

**Comment.** Select some text and click the **Comment** bubble that appears, or click a paragraph and use **Add comment** in the Comments panel (Ctrl+Alt+M does the same). Charts can't be commented on directly; comment on their title or caption. In the panel you can reply, resolve, reopen and delete. Clicking a comment scrolls to its text. If the text a comment was on has been rewritten so it can't be found, the comment is marked **detached** rather than lost.

**Save.** Click **Save** or press Ctrl+S (Cmd+S). Edits go to `report.edit.html` and comments to `report.comments.json`; the original is never written to. Reopening the file later resumes from them. If either file was changed by something else since the page was loaded (an agent, another tab), the save is refused and you're offered a reload instead of overwriting.

**Hand comments to an AI agent.** Click **Agent prompt**. It saves first if needed, then shows a short prompt to paste into your agent: the working copy's path, the open comments (with the text each is on and any replies), and an instruction to use the `editable-html` skill. The skill (in [htmlreview/skills/editable-html/SKILL.md](htmlreview/skills/editable-html/SKILL.md)) explains how the tool's files work, so the agent is free to make any change the comments ask for (wording, structure, styling, charts) without breaking the review. For Claude Code, install it once:

```
uv run htmlreview --install-skill
```

This copies it to `~/.claude/skills/editable-html/`. The prompt also gives the skill file's path, so agents without it installed can read it directly. Leave the review tab open while the agent works.

**Seeing the agent's changes.** The tab checks every couple of seconds whether the working copy or comments file changed on disk. Once the changes settle, it reloads by itself, keeping your scroll position and the comments panel, and says what changed (for example "2 comments resolved, 1 new reply"); resolved comments are shown so you can read the agent's notes. If you have unsaved changes or are in the middle of editing or writing a comment, it doesn't reload; a notice with a **Reload** button appears instead.

**Publish.** Click **Publish**. After a confirmation that says how many comments will be deleted, it writes `report.published.html` next to the original with every edit applied and nothing from the editor left in it, then deletes the working copy and comments. You can then view the published file or open another one.

### Try it on the examples

`examples/` has three fake reports:

- `sales-report/report.html`: Chart.js (bundled locally), local CSS and image, KPI numbers filled in by JavaScript, a large embedded JSON data block
- `ops-review/report.html`: Plotly from a CDN, a hand-drawn inline SVG chart, nested lists, a blockquote, Windows line endings
- `legacy-export/report.htm`: ECharts from a CDN, windows-1252 encoding, no `<head>`/`<body>`, paragraphs without closing tags

The tool writes its files next to the examples; they're ignored by git.

## What can be edited

Paragraphs, headings, list items, table cells, captions and standalone `<span>`s whose content is plain text with inline formatting (bold, links and so on). Charts, SVG, and anything inside `<head>`, `<script>` or `<style>` are never touched. Elements that rely on implicit closing tags (common in old exporters) are not editable yet.

Saving never serializes the browser's live page. Only the edited elements' contents are sent, and the server splices them into the source file by position, so everything else (scripts, embedded data, formatting, line endings, encoding) stays byte-identical.

## Files the tool writes

Next to `report.html`:

| File | Purpose | Lifetime |
| --- | --- | --- |
| `report.edit.html` | Working copy: the original plus `data-eid` attributes and your edits | Written on every save, deleted on publish |
| `report.comments.json` | Comments | Written on every save, deleted on publish |
| `report.published.html` | Clean output | Written on publish |

## For agents

Everything an agent needs is in the `editable-html` skill: [htmlreview/skills/editable-html/SKILL.md](htmlreview/skills/editable-html/SKILL.md). In short: edit `report.edit.html` (never the original), leave existing `data-eid` attributes in place, and record what was done in `report.comments.json` by resolving each comment with a one-line `resolution`, or replying when not acting. The comments file's JSON Schema is in [schema/comments.schema.json](schema/comments.schema.json).

## Development

```
uv run pytest
```
