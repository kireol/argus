// Argus Demo web example client.
//
// Home screen: counter + a colour swatch that reflects the theme.
// Settings screen: a "Dark theme" toggle and a "Back" control.
//
// Backend sync: polls the example backend's GET /api/state every 500ms and
// applies counter/theme from it; local changes POST to the backend and to
// this server's own /test/state so instrumentation stays in sync. If the
// backend is unreachable the app keeps running standalone (fetch errors are
// swallowed).
(function () {
  "use strict";

  const BACKEND_BASE = "http://127.0.0.1:8765";
  const POLL_MS = 500;

  const state = {
    counter: 0,
    theme: "light",
    screen: "home",
  };

  const els = {};

  function cacheEls() {
    els.home = document.getElementById("view-home");
    els.settings = document.getElementById("view-settings");
    els.counter = document.getElementById("counter");
    els.inc = document.getElementById("inc");
    els.settingsBtn = document.getElementById("settings");
    els.dark = document.getElementById("dark");
    els.back = document.getElementById("back");
  }

  function applyState() {
    els.counter.textContent = "Count: " + state.counter;
    els.dark.checked = state.theme === "dark";
    document.body.classList.toggle("dark", state.theme === "dark");
    const onHome = state.screen === "home";
    els.home.hidden = !onHome;
    els.settings.hidden = onHome;
  }

  function reportState() {
    // Same-origin: no CORS preflight, JSON content-type is fine here.
    fetch("/test/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        counter: state.counter,
        theme: state.theme,
        screen: state.screen,
      }),
    }).catch(function () {});
  }

  function pushBackend(partial) {
    // Cross-origin to the example backend: no explicit Content-Type, so the
    // request stays a CORS-safelisted "simple request" (no preflight) --
    // the backend's json.loads() does not care about the content type.
    fetch(BACKEND_BASE + "/api/state", {
      method: "POST",
      body: JSON.stringify(partial),
    }).catch(function () {});
  }

  function pollBackend() {
    fetch(BACKEND_BASE + "/api/state")
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        let changed = false;
        if (typeof data.counter === "number" && data.counter !== state.counter) {
          state.counter = data.counter;
          changed = true;
        }
        if (typeof data.theme === "string" && data.theme !== state.theme) {
          state.theme = data.theme;
          changed = true;
        }
        if (changed) {
          applyState();
          reportState();
        }
      })
      .catch(function () {});
  }

  function increment() {
    state.counter += 1;
    console.log("Counter: " + state.counter);
    applyState();
    reportState();
    pushBackend({ counter: state.counter });
  }

  function goSettings() {
    state.screen = "settings";
    console.log("Screen: settings");
    applyState();
    reportState();
  }

  function goHome() {
    state.screen = "home";
    console.log("Screen: home");
    applyState();
    reportState();
  }

  function toggleDark() {
    state.theme = els.dark.checked ? "dark" : "light";
    console.log("Theme: " + state.theme);
    applyState();
    reportState();
    pushBackend({ theme: state.theme });
  }

  function init() {
    cacheEls();
    applyState();
    console.log("App ready");
    reportState();

    els.inc.addEventListener("click", increment);
    els.settingsBtn.addEventListener("click", goSettings);
    els.back.addEventListener("click", goHome);
    els.dark.addEventListener("change", toggleDark);

    pollBackend();
    setInterval(pollBackend, POLL_MS);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
