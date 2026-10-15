---
name: editable-html
description: How to edit an HTML report that is under review in the HTML Review tool (files named *.edit.html with a matching *.comments.json), so that review comments get addressed and the tool keeps working. Use when asked to address, apply or resolve review comments on such a file.
---

# Editing a report under review in HTML Review

HTML Review is a local tool where a person edits an HTML report in the browser and leaves comments. You are asked to address those comments. Next to the original report there are:

| File | What it is |
| --- | --- |
| `report.html` | The original. **Never modify it.** |
| `report.edit.html` | The working copy. **Make all your changes here.** |
| `report.comments.json` | The review comments. Update it to say what you did. |

The reviewer usually has the report open in the tool while you work. It notices when you save either file and reloads by itself.

## Making changes

You can change anything the comments ask for: wording, structure, sections, formatting, colours, fonts, CSS, scripts, charts, data. Use whatever approach and skills suit the request. Only these conventions matter to the tool:

- **`data-eid` attributes.** Many elements carry `data-eid="e12"` and so on. Comments find their text through these attributes. Leave them alone on elements you keep, even if you rewrite their content. Don't change their values, and don't copy them onto new elements: if you duplicate an element, remove the attribute from the copy. New elements don't need one; the tool numbers them the next time the file is opened. Deleting an element along with its `data-eid` is fine.
- **Keep new text editable.** The reviewer can only edit text inside elements like `<p>`, `<h1>`–`<h6>`, `<li>`, `<td>`, `<th>`, `<figcaption>` that have an explicit closing tag and contain only text and inline markup (`<b>`, `<em>`, `<a>`, `<span>`, …). So write `<p>…</p>`, not a bare `<p>`, and avoid putting block elements inside them.
- **No editor artifacts.** Don't add `contenteditable`, `data-hr-*` attributes, or anything from the review tool. Publishing removes `data-eid`, so you don't need to clean those up.
- **Write each file in one go**, not in many small saves, so the reviewer's tab reloads once.

## Updating report.comments.json

Each comment looks like this:

```json
{
  "id": "c7",
  "anchor": { "eid": "e42", "quote": "win rate rose 4%", "prefix": "In EMEA, ", "suffix": " week over week" },
  "body": "Is this significant? Add a CI.",
  "status": "open",
  "author": "alex",
  "created": "2026-10-15T10:10:00-07:00",
  "replies": [],
  "resolution": null
}
```

- `anchor.eid` is the `data-eid` of the element the comment is on. `quote` is the exact text it's about, with `prefix`/`suffix` around it; without a quote, the comment is about the whole element. Some comments are about more than their anchor ("restructure this section"); the anchor just shows where the reviewer was.
- When you've addressed a comment, set `"status": "resolved"` and set `"resolution"` to one short line saying what you changed.
- If you're not acting on a comment (it's unclear, or you disagree), leave it `"open"` and append a reply: `{"author": "agent", "body": "…", "created": "<now, ISO 8601 with UTC offset>"}`.
- If you deleted the element a comment was anchored to, still resolve it and say so in the resolution.
- Don't delete comments, change their ids, or rewrite the reviewer's text. Keep the file valid JSON with the same structure.
