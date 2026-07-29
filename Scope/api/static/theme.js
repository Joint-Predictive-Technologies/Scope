/* ============================================================================
   Theme toggle — dark is the DEFAULT and is untouched; light is additive.

   Loaded SYNCHRONOUSLY in <head>, before any paint, so the stored choice is
   applied to <html> before the first frame. Deferring it produces a dark flash
   on every load for a light-theme user, which is worse than no toggle.

   Precedence: an explicit stored choice > the OS `prefers-color-scheme` > dark.
   Choosing manually pins it (stored); until then the OS preference is followed
   live, so a user who never touches the control still gets what their system
   asks for.
   ============================================================================ */
(function () {
  "use strict";
  var KEY = "scope-theme";

  function stored() {
    try { return localStorage.getItem(KEY); } catch (_) { return null; }   // private mode
  }
  function systemPrefersLight() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  }
  /* A page may pin itself to one theme by setting `data-theme-lock` on <html>
     BEFORE this script runs. That is for a surface whose colour is not a token
     swap — /osint is a WebGL globe whose clear colour, earth tint, lighting and
     severity markers were tuned together for a dark field, and the chrome sits
     over the canvas, so a light nav on a black stage would be worse than an
     honest dark page. The lock is a stated design decision, not a holdout: it
     is declared in the page and read here, so it cannot drift silently. */
  function locked() {
    var l = document.documentElement.getAttribute("data-theme-lock");
    return l === "light" || l === "dark" ? l : null;
  }
  function resolve() {
    var lock = locked();
    if (lock) return lock;
    var s = stored();
    if (s === "light" || s === "dark") return s;
    return systemPrefersLight() ? "light" : "dark";
  }
  function apply(theme) {
    // Dark is the absence of the attribute, so the default stylesheet is
    // literally what ships today — no light rule can affect it.
    if (theme === "light") document.documentElement.setAttribute("data-theme", "light");
    else document.documentElement.removeAttribute("data-theme");
    document.documentElement.style.colorScheme = theme;   // native form controls/scrollbars
  }

  apply(resolve());                                        // before first paint

  // Follow the OS while the user has expressed no preference of their own.
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: light)");
    var onChange = function () { if (!stored()) apply(resolve()); };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  window.setTheme = function (theme) {
    try { localStorage.setItem(KEY, theme); } catch (_) {}
    apply(theme);
    document.dispatchEvent(new CustomEvent("scope:themechange", { detail: { theme: theme } }));
  };
  /* Read a design token at runtime. JS colour maps (chart hues, severity
     ramps, canvas fills) cannot use var() — a canvas fillStyle or an inline
     string needs a resolved value — so they call this instead of hardcoding a
     hex, and therefore follow the theme like everything else. */
  window.themeColor = function (name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback || "";
    } catch (_) { return fallback || ""; }
  };

  window.getTheme = function () {
    return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  };

  /* The control itself is injected into the nav, next to ⌘K, so it appears on
     every page from one definition rather than being pasted per page. */
  function injectToggle() {
    var nav = document.querySelector("nav");
    if (!nav || nav.querySelector(".theme-btn")) return;
    // On a locked page the control would lie — it cannot change this page. Say
    // so instead of shipping a dead button; the choice still applies elsewhere.
    if (locked()) {
      var note = document.createElement("span");
      note.className = "theme-locked-note";
      note.style.cssText = "font-family:var(--font-mono);font-size:0.62rem;color:var(--text-tertiary);" +
                           "flex-shrink:0;margin-left:8px;letter-spacing:0.04em";
      note.textContent = "DARK STAGE";
      note.title = "This view stays dark by design — the globe's severity markers, " +
                   "earth tint and lighting are tuned as one system for a dark field.";
      nav.appendChild(note);
      return;
    }
    var style = document.getElementById("scope-theme-style");
    if (!style) {
      style = document.createElement("style");
      style.id = "scope-theme-style";
      style.textContent =
        ".theme-btn{font-family:var(--font-mono);font-size:0.72rem;line-height:1;" +
        "color:var(--text-tertiary);background:var(--surface-1);border:1px solid var(--border-subtle);" +
        "padding:5px 9px;border-radius:4px;cursor:pointer;flex-shrink:0;margin-left:8px}" +
        ".theme-btn:hover{color:var(--accent);border-color:var(--accent)}" +
        ".theme-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}";
      document.head.appendChild(style);
    }
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "theme-btn";
    var paint = function () {
      var light = window.getTheme() === "light";
      btn.textContent = light ? "☾" : "☀";
      btn.title = light ? "Switch to dark theme" : "Switch to light theme";
      btn.setAttribute("aria-label", btn.title);
    };
    paint();
    btn.addEventListener("click", function () {
      window.setTheme(window.getTheme() === "light" ? "dark" : "light");
      paint();
    });
    document.addEventListener("scope:themechange", paint);
    // After the ⌘K button when it exists, so the pair reads as one cluster.
    nav.appendChild(btn);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectToggle);
  } else {
    injectToggle();
  }
})();
