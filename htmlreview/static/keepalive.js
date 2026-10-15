// Tells the HTML Review server this tab is open, so it can exit by itself
// once every tab has been closed. Injected into every page the tool serves.
(() => {
  "use strict";
  if (window.__hrKeepalive) return;
  const token = document.currentScript && document.currentScript.dataset.hrToken;
  if (!token) return;
  const id = (crypto.randomUUID && crypto.randomUUID()) || String(Math.random()).slice(2);
  let stopped = false;
  const send = (state, keepalive = false) => {
    if (stopped) return;
    fetch("/api/presence", {
      method: "POST",
      keepalive,
      headers: { "Content-Type": "application/json", "X-HR-Token": token },
      body: JSON.stringify({ id, state }),
    }).catch(() => {});
  };
  send("hello");
  setInterval(() => send("beat"), 20000);
  window.addEventListener("pagehide", () => send("bye", true));
  // Back/forward cache: the page comes back without reloading.
  window.addEventListener("pageshow", (e) => { if (e.persisted) send("hello"); });
  window.__hrKeepalive = { stop() { stopped = true; } };
})();
