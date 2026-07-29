/* ============================================================================
   Command palette (⌘K) — ONE shared implementation for every page.

   Why this file exists: the ⌘K control was copy-pasted into each page's markup.
   17 pages had it and 13 did not (api_docs, cluster, clusters, congress_digest,
   foreign_influence, forming, intelligence, lobbying, performance, sector,
   status, thesis, universe), so the control appeared and vanished as you moved
   between tabs. Anything duplicated 17 times drifts; the fix has to live in one
   place that every page loads.

   Include on a page with:  <script src="/cmdk.js" defer></script>
   Nothing else. Do not re-add a per-page ⌘K button — placement is owned here.

   Idempotent by design:
     * the button is injected only if the page has a <nav> and no button yet;
     * the palette markup/CSS/handlers are injected only if the page does not
       already ship its own (`window.openCmd`). The homepage has a richer
       variant, so this defers to it rather than fighting it.
   ============================================================================ */
(function () {
  "use strict";

  var CSS = `
#cmd-backdrop { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.65); z-index:9000; backdrop-filter:blur(4px); }
#cmd-backdrop.open { display:flex; align-items:flex-start; justify-content:center; padding-top:12vh; }
#cmd-box { background:#1a1814; border:1px solid #3a3428; border-radius:10px; width:min(640px,92vw); overflow:hidden; }
#cmd-input { width:100%; background:transparent; border:none; border-bottom:1px solid #2a2620; outline:none; font-family:var(--font-mono); font-size:0.9rem; color:#e8e0cc; padding:1rem 1.2rem; }
#cmd-results { max-height:360px; overflow-y:auto; }
.cmd-group-label { font-family:var(--font-mono); font-size:0.6rem; letter-spacing:0.12em; text-transform:uppercase; color:#7a7060; padding:0.6rem 1.2rem 0.2rem; }
.cmd-item { display:flex; align-items:center; gap:0.8rem; padding:0.65rem 1.2rem; cursor:pointer; text-decoration:none; border-left:2px solid transparent; }
.cmd-item:hover,.cmd-item.active { background:#222018; border-left-color:#c8922a; }
.cmd-item-icon { font-size:0.9rem; opacity:0.6; width:18px; text-align:center; flex-shrink:0; }
.cmd-item-main { font-family:var(--font-mono); font-size:0.75rem; color:#e8e0cc; }
.cmd-item-sub  { font-size:0.68rem; color:#7a7060; margin-top:1px; }
.cmd-badge { font-family:var(--font-mono); font-size:0.55rem; padding:1px 5px; border-radius:2px; margin-left:auto; flex-shrink:0; }
.cmd-badge-CRITICAL { background:rgba(229,91,77,0.2); color:#e55b4d; }
.cmd-badge-HIGH     { background:rgba(200,146,42,0.2); color:#c8922a; }
.cmd-badge-MEDIUM   { background:rgba(106,176,224,0.2); color:#6ab0e0; }
.cmd-empty { font-family:var(--font-mono); font-size:0.75rem; color:#7a7060; padding:1.5rem 1.2rem; text-align:center; }
.cmd-hint { font-family:var(--font-mono); font-size:0.58rem; color:#7a7060; padding:0.5rem 1.2rem; border-top:1px solid #2a2620; display:flex; gap:1.2rem; }
.cmd-hint kbd { background:#2a2620; border:1px solid #3a3428; border-radius:3px; padding:1px 5px; font-family:inherit; font-size:0.58rem; color:#c8bfa8; }
`;

  /* The trigger's own styling is SEPARATE from the palette's, because the
     palette is skipped on pages that ship their own. When these rules lived in
     the palette block they were skipped too, and the 17 legacy pages rendered a
     white default OS button in a dark nav — a regression on pages that had a
     correct button before. This block is always injected.
     margin-left:auto pins it to the right edge of any flex nav. */
  var BTN_CSS = `
.cmdk-btn { font-family:var(--font-mono); font-size:0.65rem; color:#7a7060; background:#111009;
            border:1px solid #2a2620; padding:4px 10px; border-radius:4px; cursor:pointer;
            margin-left:auto; flex-shrink:0; white-space:nowrap; line-height:1.4; }
.cmdk-btn:hover { color:var(--amber, #c8922a); border-color:var(--amber, #c8922a); }
.cmdk-btn:focus-visible { outline:2px solid var(--amber, #c8922a); outline-offset:2px; }
`;

  var PALETTE_HTML =
    '<div id="cmd-box">' +
    '<input id="cmd-input" placeholder="Search tickers, members, headlines…" autocomplete="off" />' +
    '<div id="cmd-results"><div class="cmd-empty">Type to search…</div></div>' +
    '<div class="cmd-hint"><span><kbd>↑↓</kbd> navigate</span>' +
    '<span><kbd>↵</kbd> open</span><span><kbd>esc</kbd> close</span></div>' +
    '</div>';

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      function (c) { return "&#" + c.charCodeAt(0) + ";"; });
  }

  function injectStyle(css, id) {
    if (document.getElementById(id)) return;
    var style = document.createElement("style");
    style.id = id;
    style.textContent = css;
    document.head.appendChild(style);
  }

  /* ── the trigger: one definition, every page ── */
  function injectButton() {
    var nav = document.querySelector("nav");
    if (!nav) return;                                  // page has no nav bar
    injectStyle(BTN_CSS, "cmdk-btn-style");            // ALWAYS — see BTN_CSS note
    if (nav.querySelector(".cmdk-btn")) return;        // already placed
    var btn = document.createElement("button");
    btn.className = "cmdk-btn";
    btn.type = "button";
    btn.textContent = "⌘K";
    btn.setAttribute("aria-label", "Open search (Command K)");
    btn.addEventListener("click", function () {
      if (typeof window.openCmd === "function") window.openCmd();
    });
    nav.appendChild(btn);
  }

  /* ── the palette, only when the page does not ship its own ── */
  function injectPalette() {
    if (typeof window.openCmd === "function") return;  // legacy page owns it
    if (document.getElementById("cmd-backdrop")) return;

    injectStyle(CSS, "cmdk-palette-style");

    var back = document.createElement("div");
    back.id = "cmd-backdrop";
    back.innerHTML = PALETTE_HTML;
    back.addEventListener("click", function (e) { if (e.target === back) window.closeCmd(); });
    document.body.appendChild(back);

    var active = -1, timer = null;
    var input = back.querySelector("#cmd-input");
    var results = back.querySelector("#cmd-results");

    window.openCmd = function () { back.classList.add("open"); input.focus(); active = -1; };
    window.closeCmd = function () {
      back.classList.remove("open");
      input.value = "";
      results.innerHTML = '<div class="cmd-empty">Type to search…</div>';
      active = -1;
    };

    document.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); window.openCmd(); return; }
      if (e.key === "Escape") { window.closeCmd(); return; }
      if (!back.classList.contains("open")) return;
      var items = results.querySelectorAll(".cmd-item");
      if (e.key === "ArrowDown") {
        e.preventDefault(); active = Math.min(active + 1, items.length - 1);
        items.forEach(function (el, i) { el.classList.toggle("active", i === active); });
      }
      if (e.key === "ArrowUp") {
        e.preventDefault(); active = Math.max(active - 1, 0);
        items.forEach(function (el, i) { el.classList.toggle("active", i === active); });
      }
      if (e.key === "Enter" && active >= 0) { if (items[active]) items[active].click(); window.closeCmd(); }
    });

    input.addEventListener("input", function (e) {
      clearTimeout(timer);
      var q = e.target.value.trim();
      timer = setTimeout(function () { search(q); }, 200);
    });

    function search(q) {
      active = -1;
      if (!q || q.length < 2) {
        results.innerHTML = '<div class="cmd-empty">Type to search…</div>';
        return;
      }
      var ctl = new AbortController();
      setTimeout(function () { ctl.abort(); }, 5000);
      fetch("/api/search?q=" + encodeURIComponent(q), { signal: ctl.signal })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var html = "";
          if (d.tickers && d.tickers.length) {
            html += '<div class="cmd-group-label">Tickers</div>';
            html += d.tickers.map(function (t) {
              return '<a class="cmd-item" href="/ticker/' + encodeURIComponent(t.ticker) + '">' +
                '<span class="cmd-item-icon">◎</span><div><div class="cmd-item-main">' +
                esc(t.ticker) + "</div></div></a>";
            }).join("");
          }
          if (d.members && d.members.length) {
            html += '<div class="cmd-group-label">Members</div>';
            html += d.members.map(function (m) {
              return '<a class="cmd-item" href="/member/' + encodeURIComponent(m.bioguide_id) + '">' +
                '<span class="cmd-item-icon">◈</span><div><div class="cmd-item-main">' +
                esc(m.full_name) + '</div><div class="cmd-item-sub">' +
                esc(m.state || "") + " · " + esc(m.party || "") + "</div></div></a>";
            }).join("");
          }
          if (d.headlines && d.headlines.length) {
            html += '<div class="cmd-group-label">Signals</div>';
            html += d.headlines.map(function (h) {
              var sev = h.severity || "MEDIUM";
              return '<a class="cmd-item" href="/feed?ticker=' +
                encodeURIComponent(h.ticker || "") + '">' +
                '<span class="cmd-item-icon">⚡</span><div><div class="cmd-item-main">' +
                esc((h.headline || "").slice(0, 72)) + '</div><div class="cmd-item-sub">' +
                esc(h.ticker || "") + " · " + esc(h.rule || "") + "</div></div>" +
                '<span class="cmd-badge cmd-badge-' + esc(sev) + '">' + esc(sev) + "</span></a>";
            }).join("");
          }
          results.innerHTML = html || '<div class="cmd-empty">No matches.</div>';
        })
        .catch(function () {
          results.innerHTML = '<div class="cmd-empty">Search unavailable.</div>';
        });
    }
  }

  // Palette first so `window.openCmd` exists before the button binds to it;
  // the button's own styling is injected regardless of that branch.
  function init() { injectPalette(); injectButton(); }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
