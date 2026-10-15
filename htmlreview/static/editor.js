// HTML Review editor. Injected into the served page only; never saved to disk.
// All UI lives in a Shadow DOM so the report's CSS and ours can't collide.
// The page itself is only touched through data-hr-* attributes and
// contenteditable, which the server never reads back: saves send just
// {eid: innerHTML} for edited elements.
(() => {
  "use strict";
  if (window.__hr) return;

  const cfgEl = document.getElementById("__hr-config");
  if (!cfgEl) return;
  const cfg = JSON.parse(cfgEl.textContent);

  const INLINE = new Set(("a abbr b bdi bdo br cite code data del dfn em font i ins kbd mark q s " +
    "samp small span strong sub sup time u var wbr").split(" "));
  const CONTEXT = 32; // characters of prefix/suffix stored with a quote

  const state = {
    version: cfg.version,
    snapshots: new Map(), // eid -> clean innerHTML as last saved
    dirty: new Set(), // eids with unsaved edits
    comments: structuredClone(cfg.comments || []),
    commentsDirty: false,
    editing: null, // element being edited
    current: null, // last clicked tagged element (for element comments)
    active: null, // id of the comment selected in the sidebar
    showResolved: false,
    leaving: false,
    canSave: !cfg.commentsError,
    busy: false, // a save or publish is in flight
    saveSeq: 0, // bumps on every save, so a stale change-check result is ignored
  };
  const hr = (window.__hr = { cfg, state, editable: new Set(), locked: new Set() });

  // ------------------------------------------------------------ helpers

  async function api(path, body) {
    let res;
    try {
      res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-HR-Token": cfg.apiToken },
        body: JSON.stringify(body || {}),
      });
    } catch {
      return { error: "Can't reach the tool. Is it still running?" };
    }
    let data;
    try { data = await res.json(); } catch { data = {}; }
    if (!res.ok && !data.error) data.error = data.detail || `HTTP ${res.status}`;
    return data;
  }

  function h(tag, props, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null && c !== false) el.append(c);
    return el;
  }

  const norm = (s) => s.replace(/ /g, " ").replace(/\s+/g, " ").trim();
  const byEid = (eid) => document.querySelector(`[data-eid="${CSS.escape(eid)}"]`);
  const eidOf = (node) => {
    const el = node && (node.nodeType === 1 ? node : node.parentElement);
    return el && el.closest ? el.closest("[data-eid]") : null;
  };
  const inUI = (e) => e.composedPath().includes(host);

  function nowIso() {
    const d = new Date(), off = -d.getTimezoneOffset();
    const p = (n) => String(Math.floor(Math.abs(n))).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:` +
      `${p(d.getMinutes())}:${p(d.getSeconds())}${off >= 0 ? "+" : "-"}${p(off / 60)}:${p(off % 60)}`;
  }

  function when(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }

  // --------------------------------------------------- page-level styles

  const pageStyle = document.createElement("style");
  pageStyle.setAttribute("data-hr", "");
  pageStyle.textContent = `
    [data-hr-editable]:hover { outline: 1.5px dashed rgba(36,87,214,.55); outline-offset: 2px; cursor: text; }
    [data-hr-locked]:hover { outline: 1.5px dashed rgba(180,100,9,.55); outline-offset: 2px; }
    [data-hr-editable][data-hr-editing] { outline: 2px solid rgba(36,87,214,.85); outline-offset: 2px; }
    [data-hr-dirty] { box-shadow: -4px 0 0 0 rgba(36,87,214,.55); }
    [data-hr-commented] { outline: 1.5px dotted rgba(214,140,0,.9); outline-offset: 2px; }
    [data-hr-commented][data-hr-active] { outline: 2.5px solid rgba(214,140,0,.95); }
    [data-hr-flash] { animation: __hr-flash 1.2s ease-out; }
    @keyframes __hr-flash { from { background-color: rgba(255,190,0,.45); } to { background-color: transparent; } }
    ::highlight(hr-comment) { background-color: rgba(255,196,0,.32); }
    ::highlight(hr-comment-active) { background-color: rgba(255,160,0,.65); }`;
  (document.head || document.documentElement).appendChild(pageStyle);

  // ------------------------------------------------------------- UI shell

  const host = document.createElement("div");
  host.id = "__hr-root";
  host.setAttribute("data-hr", "");
  host.style.cssText = "all:initial;position:fixed;top:12px;right:12px;z-index:2147483647;";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <link rel="stylesheet" href="/__hr/editor.css">
    <div class="bar">
      <button class="icon collapse" title="Collapse toolbar" aria-label="Collapse toolbar">◧</button>
      <div class="file"><span class="name"></span><span class="status"></span></div>
      <div class="actions">
        <button class="open" title="Open another HTML file">Open…</button>
        <button class="prompt-btn" title="Get a prompt that asks an AI agent to address the open comments">Agent prompt</button>
        <button class="comments-btn" title="Show comments (Ctrl+Alt+M adds one)">Comments <span class="count"></span></button>
        <button class="save primary" title="Save (Ctrl+S)">Save</button>
        <button class="publish" title="Write a clean copy and finish this review">Publish</button>
        <button class="exit" title="Stop HTML Review (it also stops by itself shortly after you close every tab)">Exit</button>
      </div>
    </div>
    <div class="toasts"></div>
    <aside class="sidebar" hidden>
      <header>
        <strong>Comments</strong>
        <label class="toggle"><input type="checkbox" class="show-resolved"> Show resolved</label>
        <button class="icon close" title="Close" aria-label="Close comments">✕</button>
      </header>
      <div class="add-row">
        <button class="add primary">Add comment</button>
        <span class="hint">Select text, or click a paragraph, then add a comment.</span>
      </div>
      <div class="list"></div>
    </aside>
    <button class="bubble" hidden>Comment</button>
    <div class="modal-wrap" hidden></div>`;
  document.documentElement.appendChild(host);

  const $ = (sel) => root.querySelector(sel);
  const bar = $(".bar");
  $(".name").textContent = cfg.name;
  $(".name").title = cfg.path;

  $(".collapse").addEventListener("click", () => bar.classList.toggle("collapsed"));

  function toast(text, kind = "info", ms, action) {
    const el = h("div", { class: `toast ${kind}`, role: "status" },
      h("span", { text }),
      action ? h("button", { class: "primary", text: action.label, onclick: action.onclick }) : null,
      h("button", { class: "icon", "aria-label": "Dismiss", text: "✕", onclick: () => el.remove() }));
    $(".toasts").append(el);
    if (ms !== 0) setTimeout(() => el.remove(), ms || (kind === "error" ? 9000 : 3000));
    return el;
  }
  hr.toast = toast;

  function modal({ title, body, buttons, wide }) {
    return new Promise((resolve) => {
      const wrap = $(".modal-wrap");
      const close = (value) => { wrap.hidden = true; wrap.replaceChildren(); resolve(value); };
      const box = h("div", { class: `modal${wide ? " wide" : ""}`, role: "dialog", "aria-modal": "true" },
        h("h2", { text: title }),
        ...(Array.isArray(body) ? body : [body]).map((b) => (typeof b === "string" ? h("p", { text: b }) : b)),
        h("div", { class: "modal-actions" },
          buttons.map((b) => h("button", { class: b.kind || "", text: b.label, onclick: () => close(b.value) }))));
      wrap.replaceChildren(box);
      wrap.hidden = false;
      // A modal without buttons (the "stopped" notice) can't be dismissed.
      wrap.onclick = buttons.length ? (e) => { if (e.target === wrap) close(null); } : null;
      wrap.onkeydown = buttons.length ? (e) => { if (e.key === "Escape") close(null); } : null;
      const focus = box.querySelector("button.primary, button.danger") || box.querySelector("button");
      if (focus) focus.focus();
    });
  }

  // ----------------------------------------------------- unsaved state

  const unsaved = () => state.dirty.size > 0 || state.commentsDirty;

  function refreshStatus() {
    const n = state.dirty.size;
    const parts = [];
    if (n) parts.push(`${n} unsaved edit${n > 1 ? "s" : ""}`);
    if (state.commentsDirty) parts.push("unsaved comments");
    if (!parts.length) {
      parts.push(`${hr.editable.size} editable`);
      if (hr.locked.size) parts.push(`${hr.locked.size} script-filled`);
      if (cfg.resumed) parts.push("resumed");
    }
    $(".status").textContent = parts.join(" · ");
    $(".save").disabled = !state.canSave || !unsaved();
    const open = state.comments.filter((c) => c.status === "open").length;
    $(".count").textContent = open ? String(open) : "";
  }

  window.addEventListener("beforeunload", (e) => {
    if (unsaved() && !state.leaving) { e.preventDefault(); e.returnValue = ""; }
  });

  async function confirmDiscard(action) {
    if (!unsaved()) return true;
    const choice = await modal({
      title: "Unsaved changes",
      body: `You have unsaved changes in ${cfg.name}.`,
      buttons: [
        { label: "Cancel", value: null },
        { label: `Discard and ${action}`, value: "discard", kind: "danger" },
        { label: `Save and ${action}`, value: "save", kind: "primary" },
      ],
    });
    if (choice === "save") return await save();
    return choice === "discard";
  }

  function leaveTo(url) {
    state.leaving = true;
    location.href = url;
  }

  // ------------------------------------------- classify + clean markup

  // Text that JavaScript replaced after load (a KPI filled in by a script)
  // must never be saved back, so those elements become comment-only.
  function classify() {
    for (const el of document.querySelectorAll("[data-eid]")) {
      const eid = el.getAttribute("data-eid");
      if (!(eid in cfg.editable)) continue;
      if (norm(el.textContent) === cfg.editable[eid]) {
        el.setAttribute("data-hr-editable", "");
        hr.editable.add(eid);
        state.snapshots.set(eid, cleanHTML(el));
      } else {
        el.setAttribute("data-hr-locked", "");
        el.title = el.title || "Filled in by the page's script: you can comment on it but not edit it";
        hr.locked.add(eid);
      }
    }
    refreshStatus();
    renderComments();
  }

  // The markup a save sends for an element: no editor attributes, no block
  // elements or style-only spans that contenteditable sneaks in, no
  // browser-inserted non-breaking spaces.
  function cleanHTML(el) {
    const clone = el.cloneNode(true);
    const original = state.snapshots.get(el.getAttribute("data-eid"));
    const hadStyle = original == null || /\sstyle=/.test(original);
    const hadNbsp = original == null || /&nbsp;| /.test(original);
    for (const node of [...clone.querySelectorAll("*")].reverse()) {
      for (const a of [...node.attributes]) {
        if (/^(data-hr|contenteditable$|spellcheck$)/i.test(a.name)) node.removeAttribute(a.name);
      }
      const tag = node.tagName.toLowerCase();
      const styleOnlySpan = tag === "span" && !hadStyle &&
        node.attributes.length === 1 && node.hasAttribute("style");
      if (!INLINE.has(tag) || styleOnlySpan) {
        if (!INLINE.has(tag) && node.previousSibling) node.before(" ");
        node.replaceWith(...node.childNodes);
      } else if (!hadStyle) {
        node.removeAttribute("style");
      }
    }
    while (clone.lastChild && clone.lastChild.nodeName === "BR") clone.lastChild.remove();
    const walker = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT);
    for (let t = walker.nextNode(); t; t = walker.nextNode()) {
      t.data = hadNbsp
        ? t.data.replace(/ (?= )|(?<= ) /g, " ")
        : t.data.replace(/ /g, " ");
    }
    return clone.innerHTML;
  }

  function updateDirty(el) {
    const eid = el.getAttribute("data-eid");
    if (cleanHTML(el) !== state.snapshots.get(eid)) {
      state.dirty.add(eid);
      el.setAttribute("data-hr-dirty", "");
    } else {
      state.dirty.delete(eid);
      el.removeAttribute("data-hr-dirty");
    }
    refreshStatus();
  }

  // ------------------------------------------------------------ editing

  function caretAt(x, y) {
    if (document.caretPositionFromPoint) {
      const p = document.caretPositionFromPoint(x, y);
      if (p) { const r = document.createRange(); r.setStart(p.offsetNode, p.offset); return r; }
    }
    return document.caretRangeFromPoint ? document.caretRangeFromPoint(x, y) : null;
  }

  function startEditing(el, x, y) {
    if (state.editing === el) return;
    stopEditing();
    state.editing = el;
    el.setAttribute("contenteditable", "true");
    el.setAttribute("data-hr-editing", "");
    el.focus({ preventScroll: true });
    const r = x != null ? caretAt(x, y) : null;
    if (r && el.contains(r.startContainer)) {
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
    }
  }

  function stopEditing() {
    const el = state.editing;
    if (!el) return;
    state.editing = null;
    el.removeAttribute("contenteditable");
    el.removeAttribute("data-hr-editing");
    updateDirty(el);
    renderHighlights();
  }

  document.addEventListener("click", (e) => {
    if (inUI(e)) return;
    const el = eidOf(e.target);
    // Inside an editable element a click edits; Ctrl/Cmd+click follows links.
    const link = e.target.closest && e.target.closest("a[href]");
    if (el && link && el.contains(link) && el.hasAttribute("data-hr-editable") && !(e.ctrlKey || e.metaKey)) {
      e.preventDefault();
    }
    const hit = commentAt(e.clientX, e.clientY);
    if (hit) selectComment(hit.id, { scroll: false });
    if (!el) {
      if (state.editing && !state.editing.contains(e.target)) stopEditing();
      state.current = null;
      return;
    }
    state.current = el;
    const sel = getSelection();
    const selecting = sel && !sel.isCollapsed && state.editing !== el;
    if (el.hasAttribute("data-hr-editable") && !selecting) startEditing(el, e.clientX, e.clientY);
  }, true);

  document.addEventListener("focusout", (e) => {
    if (state.editing && e.target === state.editing) {
      // Clicking our own UI (e.g. the Comment bubble) shouldn't end editing
      // before it has read the selection.
      setTimeout(() => { if (state.editing && document.activeElement !== state.editing) stopEditing(); }, 0);
    }
  }, true);

  document.addEventListener("input", (e) => {
    if (state.editing && state.editing.contains(e.target)) updateDirty(state.editing);
  }, true);

  document.addEventListener("paste", (e) => {
    if (!state.editing || !state.editing.contains(e.target)) return;
    e.preventDefault();
    const text = (e.clipboardData.getData("text/plain") || "").replace(/\s*\r?\n\s*/g, " ");
    document.execCommand("insertText", false, text);
  }, true);

  document.addEventListener("drop", (e) => {
    if (state.editing && state.editing.contains(e.target)) e.preventDefault();
  }, true);

  window.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && !e.altKey && e.key.toLowerCase() === "s") {
      e.preventDefault();
      save();
      return;
    }
    if (inUI(e)) return;
    if (mod && e.altKey && e.key.toLowerCase() === "m") {
      e.preventDefault();
      startComment();
      return;
    }
    if (!state.editing) return;
    if (e.key === "Escape" || (e.key === "Enter" && !e.shiftKey)) {
      e.preventDefault();
      const el = state.editing;
      stopEditing();
      el.blur();
    }
    // Keep the page's own keyboard shortcuts from reacting to typing.
    e.stopPropagation();
  }, true);

  // --------------------------------------------------------------- save

  function collectEdits() {
    if (state.editing) updateDirty(state.editing);
    const edits = {};
    for (const eid of state.dirty) {
      const el = byEid(eid);
      if (el) edits[eid] = cleanHTML(el);
    }
    return edits;
  }

  async function handleConflict() {
    const choice = await modal({
      title: "Changed on disk",
      body: [
        "The working copy or the comments file changed since this page was loaded, " +
        "probably because another program (or another tab) edited it.",
        "Reload to see those changes. Your unsaved changes here will be lost.",
      ],
      buttons: [{ label: "Cancel", value: null }, { label: "Reload", value: "reload", kind: "primary" }],
    });
    if (choice === "reload") { state.leaving = true; location.reload(); }
  }

  async function save() {
    if (!state.canSave) {
      toast(cfg.commentsError || "Saving is disabled.", "error");
      return false;
    }
    const edits = collectEdits();
    if (!unsaved()) return true;
    $(".save").disabled = true;
    state.busy = true;
    state.saveSeq++;
    const res = await api(`/api/save?doc=${encodeURIComponent(cfg.doc)}`,
      { version: state.version, edits, comments: state.comments })
      .finally(() => { state.busy = false; });
    if (res.conflict) { refreshStatus(); handleConflict(); return false; }
    if (res.error) { refreshStatus(); toast(`Save failed: ${res.error}`, "error"); return false; }
    state.version = res.version;
    for (const eid of Object.keys(edits)) {
      state.snapshots.set(eid, edits[eid]);
      const el = byEid(eid);
      if (el) updateDirty(el);
    }
    state.commentsDirty = false;
    refreshStatus();
    toast(`Saved to ${cfg.name.replace(/(\.[^.]+)$/, ".edit$1")}`);
    return true;
  }

  $(".save").addEventListener("click", save);

  // ------------------------------------------------------- open another

  async function openAnother() {
    if (!(await confirmDiscard("open another file"))) return;
    const btn = $(".open");
    btn.disabled = true;
    try {
      let result = await api("/api/pick");
      if (result.noDialog) {
        const path = prompt("No file dialog is available. Paste the path of an HTML file:");
        if (!path) return;
        result = await api("/api/open", { path });
      }
      if (result.url) leaveTo(result.url);
      else if (result.error) toast(result.error, "error");
    } finally {
      btn.disabled = false;
    }
  }
  $(".open").addEventListener("click", openAnother);

  // --------------------------------------------------------------- exit

  async function exitTool() {
    if (!(await confirmDiscard("exit"))) return;
    await api("/api/shutdown");
    state.leaving = true;
    if (window.__hrKeepalive) window.__hrKeepalive.stop();
    bar.querySelector(".actions").replaceChildren();
    $(".status").textContent = "Stopped";
    openSidebar(false);
    modal({
      title: "HTML Review has stopped",
      body: ["You can close this tab. Run uv run htmlreview to start it again."],
      buttons: [],
    });
  }
  $(".exit").addEventListener("click", exitTool);

  // ------------------------------------------------------------ publish

  async function publish() {
    const open = state.comments.filter((c) => c.status === "open").length;
    const total = state.comments.length;
    const edits = collectEdits();
    const body = [
      `This writes ${cfg.publishedName} next to the original, with every edit applied and nothing from the editor left in it. ` +
      `The original ${cfg.name} is not changed.`,
    ];
    if (total) {
      body.push(h("p", { class: open ? "warn" : "" },
        `Publishing deletes the working copy and all ${total} comment${total > 1 ? "s" : ""}` +
        (open ? `, including ${open} that ${open > 1 ? "are" : "is"} still open.` : ".")));
    } else {
      body.push("The working copy is deleted afterwards.");
    }
    const ok = await modal({
      title: `Publish ${cfg.name}?`,
      body,
      buttons: [{ label: "Cancel", value: false }, { label: "Publish", value: true, kind: "primary" }],
    });
    if (!ok) return;
    state.busy = true;
    state.saveSeq++;
    const res = await api(`/api/publish?doc=${encodeURIComponent(cfg.doc)}`,
      { version: state.version, edits, comments: [] })
      .finally(() => { state.busy = false; });
    if (res.conflict) { handleConflict(); return; }
    if (res.error) { toast(`Publish failed: ${res.error}`, "error"); return; }
    state.leaving = true;
    const next = await modal({
      title: "Published",
      body: [`Saved as ${res.published}, next to the original.`],
      buttons: [
        { label: "View published file", value: "view" },
        { label: "Open another file", value: "open", kind: "primary" },
      ],
    });
    if (next === "view") location.href = res.url;
    else if (next === "open") {
      const result = await api("/api/pick");
      location.href = result.url || "/";
    } else location.reload();
  }
  $(".publish").addEventListener("click", publish);

  // ----------------------------------------------------- comment anchors

  // Concatenated text of an element, as Range.toString() sees it.
  function textOf(el) {
    const r = document.createRange();
    r.selectNodeContents(el);
    return r.toString();
  }

  function rangeFromOffsets(el, start, end) {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    let pos = 0, startSet = false;
    for (let t = walker.nextNode(); t; t = walker.nextNode()) {
      const len = t.data.length;
      if (!startSet && start <= pos + len) { range.setStart(t, start - pos); startSet = true; }
      if (startSet && end <= pos + len) { range.setEnd(t, end - pos); return range; }
      pos += len;
    }
    return null;
  }

  function findQuote(text, a) {
    const tries = [
      [(a.prefix || "") + a.quote + (a.suffix || ""), (a.prefix || "").length],
      [(a.prefix || "") + a.quote, (a.prefix || "").length],
      [a.quote + (a.suffix || ""), 0],
      [a.quote, 0],
    ];
    for (const [needle, shift] of tries) {
      const i = text.indexOf(needle);
      if (needle && i >= 0) return i + shift;
    }
    return -1;
  }

  // -> {el, range} | {el} for element comments | null when detached
  function resolveAnchor(c) {
    const a = c.anchor || {};
    const el = a.eid ? byEid(a.eid) : null;
    if (!a.quote) return el ? { el } : null;
    const candidates = el ? [el, ...document.querySelectorAll("[data-eid]")] : document.querySelectorAll("[data-eid]");
    for (const cand of candidates) {
      const i = findQuote(textOf(cand), a);
      if (i >= 0) {
        const range = rangeFromOffsets(cand, i, i + a.quote.length);
        if (range) return { el: cand, range };
      }
    }
    return null;
  }

  function selectionAnchor() {
    const sel = getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const r = sel.getRangeAt(0);
    const el = eidOf(r.startContainer);
    if (!el || host.contains(el)) return null;
    const pre = document.createRange();
    pre.setStart(el, 0);
    pre.setEnd(r.startContainer, r.startOffset);
    const text = textOf(el);
    let start = pre.toString().length;
    let end = el.contains(r.endContainer)
      ? start + r.toString().length
      : text.length;
    while (start < end && /\s/.test(text[start])) start++;
    while (end > start && /\s/.test(text[end - 1])) end--;
    if (start >= end) return null;
    return {
      eid: el.getAttribute("data-eid"),
      quote: text.slice(start, end),
      prefix: text.slice(Math.max(0, start - CONTEXT), start),
      suffix: text.slice(end, end + CONTEXT),
    };
  }

  // ------------------------------------------------------- highlights

  const anchors = new Map(); // comment id -> resolved anchor (or null)

  function renderHighlights() {
    anchors.clear();
    for (const el of document.querySelectorAll("[data-hr-commented]")) {
      el.removeAttribute("data-hr-commented");
      el.removeAttribute("data-hr-active");
    }
    const ranges = [], activeRanges = [];
    for (const c of state.comments) {
      const a = resolveAnchor(c);
      anchors.set(c.id, a);
      if (!a || c.status !== "open") continue;
      if (a.range) (c.id === state.active ? activeRanges : ranges).push(a.range);
      else {
        a.el.setAttribute("data-hr-commented", "");
        if (c.id === state.active) a.el.setAttribute("data-hr-active", "");
      }
    }
    if (window.CSS && CSS.highlights && window.Highlight) {
      CSS.highlights.set("hr-comment", new Highlight(...ranges));
      CSS.highlights.set("hr-comment-active", new Highlight(...activeRanges));
    }
  }

  function commentAt(x, y) {
    for (const c of state.comments) {
      if (c.status !== "open") continue;
      const a = anchors.get(c.id);
      if (!a) continue;
      const rects = a.range ? [...a.range.getClientRects()] : [];
      if (rects.some((r) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom)) return c;
    }
    return null;
  }

  // ------------------------------------------------------- comments UI

  const sidebar = $(".sidebar");
  const list = $(".list");

  function openSidebar(open = true) {
    sidebar.hidden = !open;
    $(".comments-btn").classList.toggle("on", open);
  }
  $(".comments-btn").addEventListener("click", () => openSidebar(sidebar.hidden));
  $(".close").addEventListener("click", () => openSidebar(false));
  $(".show-resolved").addEventListener("change", (e) => {
    state.showResolved = e.target.checked;
    renderComments();
  });

  function nextId() {
    let max = 0;
    for (const c of state.comments) {
      const m = /^c(\d+)$/.exec(c.id);
      if (m) max = Math.max(max, +m[1]);
    }
    return `c${max + 1}`;
  }

  function changed() {
    state.commentsDirty = true;
    renderComments();
    refreshStatus();
  }

  let draft = null; // {anchor} while composing a new comment

  function startComment(anchor) {
    anchor = anchor || selectionAnchor();
    if (!anchor && state.current) anchor = { eid: state.current.getAttribute("data-eid") };
    if (!anchor) {
      toast("Select some text, or click a paragraph, then add a comment.");
      openSidebar(true);
      return;
    }
    hideBubble();
    draft = { anchor };
    openSidebar(true);
    renderComments();
    const ta = list.querySelector(".draft textarea");
    if (ta) ta.focus();
  }
  $(".add").addEventListener("mousedown", (e) => e.preventDefault()); // keep the page selection
  $(".add").addEventListener("click", () => startComment());

  function quoteBlock(anchor, detached) {
    if (anchor.quote) return h("blockquote", { class: "quote", text: anchor.quote });
    const el = byEid(anchor.eid);
    const label = el ? norm(el.textContent).slice(0, 80) : "";
    return h("div", { class: "quote element", text: detached ? "(element no longer found)" : `On: ${label}${label.length >= 80 ? "…" : ""}` });
  }

  function textBox(placeholder, submitLabel, onSubmit, onCancel) {
    const ta = h("textarea", { rows: 3, placeholder });
    const submit = () => { const v = ta.value.trim(); if (v) onSubmit(v); };
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); }
      if (e.key === "Escape") { e.preventDefault(); onCancel(); }
      e.stopPropagation();
    });
    return h("div", { class: "compose" }, ta,
      h("div", { class: "row" },
        h("span", { class: "hint", text: "Ctrl+Enter to send" }),
        h("button", { text: "Cancel", onclick: onCancel }),
        h("button", { class: "primary", text: submitLabel, onclick: submit })));
  }

  function renderComments() {
    renderHighlights();
    const items = [];
    if (draft) {
      items.push(h("div", { class: "card draft" },
        quoteBlock(draft.anchor),
        textBox("Add a comment…", "Comment", (body) => {
          state.comments.push({
            id: nextId(), anchor: draft.anchor, body, status: "open",
            author: cfg.author, created: nowIso(), replies: [], resolution: null,
          });
          state.active = state.comments[state.comments.length - 1].id;
          draft = null;
          getSelection().removeAllRanges();
          changed();
        }, () => { draft = null; renderComments(); })));
    }
    const shown = state.comments.filter((c) => state.showResolved || c.status === "open");
    shown.sort((a, b) => docOrder(a) - docOrder(b));
    for (const c of shown) items.push(card(c));
    if (!items.length) {
      const resolved = state.comments.length;
      items.push(h("p", { class: "empty", text: resolved
        ? `No open comments. ${resolved} resolved.`
        : "No comments yet." }));
    }
    list.replaceChildren(...items);
  }

  function docOrder(c) {
    const a = anchors.get(c.id);
    if (!a) return Number.MAX_SAFE_INTEGER;
    const rect = (a.range || a.el).getBoundingClientRect();
    return rect.top + window.scrollY + rect.left / 10000;
  }

  let replying = null; // comment id with an open reply box

  function card(c) {
    const a = anchors.get(c.id);
    const detached = !a;
    const el = h("div", {
      class: `card${c.id === state.active ? " active" : ""}${c.status === "resolved" ? " resolved" : ""}`,
      "data-id": c.id,
      onclick: (e) => { if (!e.target.closest("button, textarea")) selectComment(c.id, { scroll: true }); },
    },
      h("div", { class: "meta" },
        h("strong", { text: c.author || "someone" }),
        h("span", { class: "time", text: when(c.created) }),
        detached ? h("span", { class: "tag warn", text: "detached", title: "The text this comment was on can't be found any more" }) : null,
        c.status === "resolved" ? h("span", { class: "tag", text: "resolved" }) : null),
      quoteBlock(c.anchor, detached),
      h("div", { class: "body", text: c.body }),
      c.resolution ? h("div", { class: "resolution", text: `Resolution: ${c.resolution}` }) : null,
      (c.replies || []).map((r) => h("div", { class: "reply" },
        h("div", { class: "meta" }, h("strong", { text: r.author || "someone" }), h("span", { class: "time", text: when(r.created) })),
        h("div", { class: "body", text: r.body }))),
      replying === c.id
        ? textBox("Reply…", "Reply", (body) => {
            c.replies = c.replies || [];
            c.replies.push({ author: cfg.author, body, created: nowIso() });
            replying = null;
            changed();
          }, () => { replying = null; renderComments(); })
        : h("div", { class: "row actions" },
            h("button", { text: "Reply", onclick: () => { replying = c.id; renderComments(); list.querySelector(`[data-id="${c.id}"] textarea`)?.focus(); } }),
            h("button", { text: c.status === "open" ? "Resolve" : "Reopen", onclick: () => {
              c.status = c.status === "open" ? "resolved" : "open";
              changed();
            } }),
            h("button", { class: "danger-text", text: "Delete", onclick: async () => {
              const ok = await modal({ title: "Delete comment?", body: `“${c.body.slice(0, 120)}”`,
                buttons: [{ label: "Cancel", value: false }, { label: "Delete", value: true, kind: "danger" }] });
              if (!ok) return;
              state.comments = state.comments.filter((x) => x !== c);
              changed();
            } })));
    return el;
  }

  function selectComment(id, { scroll }) {
    state.active = id;
    openSidebar(true);
    renderComments();
    list.querySelector(`[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest" });
    if (!scroll) return;
    const a = anchors.get(id);
    if (!a) { toast("This comment's text can't be found in the page any more."); return; }
    const target = a.range ? a.range.startContainer.parentElement : a.el;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    a.el.setAttribute("data-hr-flash", "");
    setTimeout(() => a.el.removeAttribute("data-hr-flash"), 1300);
  }

  // ------------------------------------------- floating Comment bubble

  const bubble = $(".bubble");
  function hideBubble() { bubble.hidden = true; }
  function maybeShowBubble() {
    const sel = getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount || !selectionAnchor()) { hideBubble(); return; }
    const rects = sel.getRangeAt(0).getClientRects();
    const last = rects[rects.length - 1];
    if (!last) { hideBubble(); return; }
    bubble.style.top = `${Math.min(window.innerHeight - 40, last.bottom + 6)}px`;
    bubble.style.left = `${Math.max(8, Math.min(window.innerWidth - 110, last.right - 40))}px`;
    bubble.hidden = false;
  }
  document.addEventListener("mouseup", (e) => { if (!inUI(e)) setTimeout(maybeShowBubble, 0); }, true);
  document.addEventListener("keyup", (e) => { if (!inUI(e) && e.shiftKey) maybeShowBubble(); }, true);
  window.addEventListener("scroll", hideBubble, true);
  bubble.addEventListener("mousedown", (e) => e.preventDefault());
  bubble.addEventListener("click", () => startComment());

  // ------------------------------------------------------- agent prompt

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      const ta = h("textarea", { style: "position:fixed;opacity:0" });
      ta.value = text;
      root.append(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    }
  }

  async function showAgentPrompt() {
    if (!state.comments.some((c) => c.status === "open")) {
      toast("There are no open comments for an agent to address.");
      return;
    }
    if (unsaved()) {
      const ok = await modal({
        title: "Save first?",
        body: "The agent works from the saved files, so your changes need to be saved first.",
        buttons: [{ label: "Cancel", value: false }, { label: "Save and continue", value: true, kind: "primary" }],
      });
      if (!ok || !(await save())) return;
    }
    const res = await api(`/api/agent-prompt?doc=${encodeURIComponent(cfg.doc)}`);
    if (res.error) { toast(res.error, "error"); return; }
    const ta = h("textarea", { class: "prompt-text", readonly: true, rows: 10 });
    ta.value = res.prompt;
    const choice = await modal({
      title: "Agent prompt",
      wide: true,
      body: [
        `Paste this into your AI agent to have it address the ${res.open} open comment${res.open > 1 ? "s" : ""}. ` +
        "Keep this tab open: it reloads by itself when the agent saves its changes.",
        ta,
        res.skillInstalled ? null : h("p", { class: "hint",
          text: "Tip: run \"uv run htmlreview --install-skill\" once so Claude Code always has the editable-html skill. " +
                "The prompt also says where to read it, so it works without installing." }),
      ].filter(Boolean),
      buttons: [{ label: "Close", value: null }, { label: "Copy prompt", value: "copy", kind: "primary" }],
    });
    if (choice === "copy") {
      if (await copyText(res.prompt)) toast("Prompt copied. Paste it into your agent.");
      else toast("Couldn't copy automatically. Open Agent prompt again and copy the text by hand.", "error");
    }
  }
  $(".prompt-btn").addEventListener("click", showAgentPrompt);

  // ------------------------------------------- reload when files change

  // An agent (or anything else) may edit the working copy and comments file
  // while this tab is open. Check every couple of seconds; once the change
  // has settled, reload so it shows up. If reloading would lose something
  // the reviewer is doing here, ask instead.
  const VIEW_KEY = `__hr-view:${cfg.doc}`;
  let pendingVersion = null;
  let changeToast = null;

  const busyHere = () => unsaved() || state.editing || draft || replying || !$(".modal-wrap").hidden;

  function reloadFromDisk() {
    try {
      sessionStorage.setItem(VIEW_KEY, JSON.stringify({
        scroll: window.scrollY,
        sidebar: !sidebar.hidden,
        active: state.active,
        showResolved: state.showResolved,
        before: state.comments.map((c) => [c.id, c.status, (c.replies || []).length]),
      }));
    } catch { /* storage unavailable: just reload */ }
    state.leaving = true;
    location.reload();
  }

  function onDiskChange() {
    if (!busyHere()) { reloadFromDisk(); return; }
    if (changeToast && changeToast.isConnected) return;
    changeToast = toast(
      unsaved()
        ? "This file or its comments changed on disk (an agent?). Reload to see the changes; your unsaved changes here will be lost."
        : "This file or its comments changed on disk. Reload to see the changes.",
      "warn", 0, { label: "Reload", onclick: reloadFromDisk });
  }

  async function checkDisk() {
    if (state.leaving || state.busy || document.hidden) return;
    const seq = state.saveSeq;
    const res = await api(`/api/status?doc=${encodeURIComponent(cfg.doc)}`);
    if (!res.version || state.leaving || state.busy || seq !== state.saveSeq) return;
    if (res.version === state.version) { pendingVersion = null; return; }
    // Wait for two identical readings in a row: the agent may still be
    // writing the second file.
    if (res.version !== pendingVersion) { pendingVersion = res.version; return; }
    onDiskChange();
  }

  function restoreView() {
    let view = null;
    try {
      view = JSON.parse(sessionStorage.getItem(VIEW_KEY) || "null");
      sessionStorage.removeItem(VIEW_KEY);
    } catch { /* ignore */ }
    if (!view) return;
    state.showResolved = view.showResolved;
    $(".show-resolved").checked = view.showResolved;
    if (view.active && state.comments.some((c) => c.id === view.active)) state.active = view.active;
    openSidebar(view.sidebar);
    renderComments();
    window.scrollTo(0, view.scroll);
    const before = new Map(view.before.map(([id, status, replies]) => [id, { status, replies }]));
    let resolved = 0, replies = 0;
    for (const c of state.comments) {
      const b = before.get(c.id);
      if (!b) continue;
      if (b.status === "open" && c.status === "resolved") resolved++;
      replies += Math.max(0, (c.replies || []).length - b.replies);
    }
    if (resolved && !state.showResolved) {
      // Show what the agent resolved, with its resolution notes.
      state.showResolved = true;
      $(".show-resolved").checked = true;
      renderComments();
    }
    const parts = [];
    if (resolved) parts.push(`${resolved} comment${resolved > 1 ? "s" : ""} resolved`);
    if (replies) parts.push(`${replies} new repl${replies > 1 ? "ies" : "y"}`);
    toast(`Reloaded with changes from disk${parts.length ? `: ${parts.join(", ")}` : ""}.`, "info", 6000);
  }

  // ------------------------------------------------------------- start

  if (cfg.commentsError) {
    toast(`${cfg.commentsError} Saving is disabled until the comments file is fixed or removed.`, "error", 0);
  }
  if (cfg.originalChanged) {
    toast(`${cfg.name} changed outside the tool since the comments were saved. Some comments may point at the wrong text.`, "warn", 0);
  }
  refreshStatus();

  function afterPageScripts() {
    // Give chart libraries and page scripts a moment after "load".
    setTimeout(() => {
      classify();
      restoreView();
      setInterval(checkDisk, 2000);
    }, 400);
  }
  if (document.readyState === "complete") afterPageScripts();
  else window.addEventListener("load", afterPageScripts, { once: true });
})();
