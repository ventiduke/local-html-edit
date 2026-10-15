# HTML Review

A local tool for reviewing HTML reports: open a file in your browser, edit text in place, leave comments, then publish a clean copy. The original file is never modified.

See [docs/PLAN.md](docs/PLAN.md) for the full design.

## Status

| Milestone | State |
| --- | --- |
| 1. Serve and inject (open files, toolbar, editable-element detection) | Done |
| 2. Edit and save | Not started |
| 3. Comments | Not started |
| 4. Publish | Not started |
| 5. Hardening | Not started |

## Running it

You need [uv](https://docs.astral.sh/uv/). It installs a suitable Python for you if you don't have one.

- **Windows (PowerShell):** `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **macOS:** `curl -LsSf https://astral.sh/uv/install.sh | sh`

Then, from this folder:

```
uv run htmlreview
```

A browser tab opens on the start page. Click **Open HTML file…** (or paste a path) to open a report. Use the toolbar's **Open…** button to switch to another file at any time. Keep the terminal window open while you work; press Ctrl+C there to quit.

Options:

```
uv run htmlreview path\to\report.html   # open a file straight away
uv run htmlreview --port 9000           # default port is 8765 (a free one is used if it's taken)
uv run htmlreview --no-browser          # don't open a browser tab
uv run htmlreview --author "Alex"       # name recorded on comments (default: your OS username)
```

### Try it on the examples

`examples/` has three fake reports:

- `sales-report/report.html`: Chart.js (bundled locally), local CSS and image, KPI numbers filled in by JavaScript, a large embedded JSON data block
- `ops-review/report.html`: Plotly from a CDN, a hand-drawn inline SVG chart, nested lists, a blockquote, Windows line endings
- `legacy-export/report.htm`: ECharts from a CDN, windows-1252 encoding, no `<head>`/`<body>`, paragraphs without closing tags

Hover over text to see what's editable (blue dashed outline). Text that the page's own JavaScript fills in, like the KPI numbers in the sales report, is outlined in orange: it can be commented on but not edited, so a script-generated value is never saved into the file.

## What can be edited

Paragraphs, headings, list items, table cells, captions and standalone `<span>`s whose content is plain text with inline formatting (bold, links and so on). Charts, SVG, and anything inside `<head>`, `<script>` or `<style>` are never touched. Elements that rely on implicit closing tags (common in old exporters) are not editable yet.

## Files the tool writes

Next to `report.html`:

| File | When |
| --- | --- |
| `report.edit.html` | Working copy, written on save (milestone 2) |
| `report.comments.json` | Comments, written on save (milestone 3) |
| `report.published.html` | Clean output, written on publish (milestone 4); the two files above are then deleted |

## Development

```
uv run pytest
```
