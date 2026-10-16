// Tells the HTML Review server this tab is open, so it can exit by itself
// once every tab has been closed. Injected into every page the tool serves.
//
// Only the goodbye on "pagehide" counts as closing. Browsers pause timers in
// background tabs, so the periodic check-in may stop for a long time; the
// events below make the tab check in again as soon as it wakes up.
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
  // Back/forward cache restores, thawed frozen tabs (Chrome "resume"),
  // windows brought back into view, regained focus or network.
  window.addEventListener("pageshow", () => send("hello"));
  document.addEventListener("resume", () => send("hello"));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") send("hello");
  });
  window.addEventListener("focus", () => send("hello"));
  window.addEventListener("online", () => send("hello"));
  window.__hrKeepalive = { id, stop() { stopped = true; } };
})();
