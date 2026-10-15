// HTML Review editor. Injected into the served page only; never saved to disk.
// All UI lives in a Shadow DOM so the report's CSS and ours can't collide.
(() => {
  "use strict";
  if (window.__hr) return;

  const cfgEl = document.getElementById("__hr-config");
  if (!cfgEl) return;
  const cfg = JSON.parse(cfgEl.textContent);
  const hr = (window.__hr = { cfg, editable: new Set(), locked: new Set() });

  // ---------------------------------------------------------------- API

  async function api(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-HR-Token": cfg.apiToken },
      body: JSON.stringify(body || {}),
    });
    let data;
    try { data = await res.json(); } catch { data = { error: `HTTP ${res.status}` }; }
    if (!res.ok && !data.error) data.error = `HTTP ${res.status}`;
    return data;
  }

  // ------------------------------------------------- page-level styles

  const pageStyle = document.createElement("style");
  pageStyle.setAttribute("data-hr", "");
  pageStyle.textContent = `
    html[data-hr-outlines] [data-hr-editable]:hover {
      outline: 1.5px dashed rgba(36, 87, 214, .55); outline-offset: 2px; cursor: text;
    }
    html[data-hr-outlines] [data-hr-locked]:hover {
      outline: 1.5px dashed rgba(180, 100, 9, .55); outline-offset: 2px;
    }`;
  document.head ? document.head.appendChild(pageStyle) : document.documentElement.appendChild(pageStyle);
  document.documentElement.setAttribute("data-hr-outlines", "");

  // ------------------------------------------------------------ toolbar

  const host = document.createElement("div");
  host.id = "__hr-root";
  host.setAttribute("data-hr", "");
  host.style.cssText = "all:initial;position:fixed;top:12px;right:12px;z-index:2147483647;";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <link rel="stylesheet" href="/__hr/editor.css">
    <div class="bar" part="bar">
      <button class="icon collapse" title="Collapse toolbar" aria-label="Collapse toolbar">◧</button>
      <div class="file">
        <span class="name"></span>
        <span class="status"></span>
      </div>
      <div class="actions">
        <button class="open">Open…</button>
        <button class="save" disabled title="Coming in milestone 2">Save</button>
        <button class="publish" disabled title="Coming in milestone 4">Publish</button>
      </div>
    </div>
    <div class="toast" role="status" hidden></div>`;
  document.documentElement.appendChild(host);

  const $ = (sel) => root.querySelector(sel);
  const bar = $(".bar");
  $(".name").textContent = cfg.name;
  $(".name").title = cfg.path;

  $(".collapse").addEventListener("click", () => {
    bar.classList.toggle("collapsed");
    $(".collapse").title = bar.classList.contains("collapsed") ? "Expand toolbar" : "Collapse toolbar";
  });

  let toastTimer;
  function toast(text, kind = "info") {
    const el = $(".toast");
    el.textContent = text;
    el.className = `toast ${kind}`;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, kind === "error" ? 8000 : 3000);
  }
  hr.toast = toast;

  $(".open").addEventListener("click", async () => {
    const btn = $(".open");
    btn.disabled = true;
    try {
      let result = await api("/api/pick");
      if (result.noDialog) {
        const path = prompt("No file dialog is available. Paste the path of an HTML file:");
        if (!path) return;
        result = await api("/api/open", { path });
      }
      if (result.url) location.href = result.url;
      else if (result.error) toast(result.error, "error");
    } catch {
      toast("Can't reach the tool. Is it still running?", "error");
    } finally {
      btn.disabled = false;
    }
  });

  // ------------------------------------------ editable elements + guard

  const norm = (s) => s.replace(/ /g, " ").replace(/\s+/g, " ").trim();

  // Text that JavaScript replaced after load (a KPI filled in by a script)
  // must never be saved back, so those elements become comment-only.
  function classify() {
    let editable = 0, locked = 0;
    for (const el of document.querySelectorAll("[data-eid]")) {
      const eid = el.getAttribute("data-eid");
      if (!(eid in cfg.editable)) continue;
      if (norm(el.textContent) === cfg.editable[eid]) {
        el.setAttribute("data-hr-editable", "");
        el.removeAttribute("data-hr-locked");
        hr.editable.add(eid);
        hr.locked.delete(eid);
        editable++;
      } else {
        el.setAttribute("data-hr-locked", "");
        el.removeAttribute("data-hr-editable");
        el.title = el.title || "Filled in by the page's script, so it can't be edited here";
        hr.locked.add(eid);
        hr.editable.delete(eid);
        locked++;
      }
    }
    $(".status").textContent =
      `${editable} editable` + (locked ? ` · ${locked} script-filled` : "") +
      (cfg.resumed ? " · resumed" : "");
  }

  function afterPageScripts() {
    // Give chart libraries and page scripts a moment after "load".
    setTimeout(classify, 400);
  }
  if (document.readyState === "complete") afterPageScripts();
  else window.addEventListener("load", afterPageScripts, { once: true });
})();
