/* ============================================================================
   DNA-AS-INTELLIGENCE-STATE — a horizontal double helix whose every rung is a
   real Scope alert.

   Subscribes to `SignalEvents`. It has no fetch of its own and no generator:
   if the bus delivers nothing, the helix is empty and says so. An idle helix
   is the honest rendering of an idle system.

   ── WHAT EACH VISUAL PROPERTY ACTUALLY MEANS ─────────────────────────────
     colour     ← event.category      the GATE's instrument (congressional,
                                      insider, contracts, earnings,
                                      institutional), read from
                                      /api/rule-model. Rules the gate excludes
                                      render as "context" grey — not a styling
                                      choice, a true statement that they cannot
                                      corroborate anything.
     thickness  ← event.severity      LOW → CRITICAL, 1.1px → 3.4px.
     glow       ← corroboration.is_corroborated
                                      layered alpha strokes, and the rung's two
                                      endpoints brighten. Scales with
                                      instrument_count when the alert carries one
                                      (RULE_10 writes it) and drops to a FLOOR
                                      when it does not, so "breadth unknown" is
                                      visibly weaker than a measured 3. Saturates
                                      at 5, matching the gate's own tiers.
     fade       ← age                 full at 0h, floor at MAX_AGE_MS.

   ⚠️ NOTHING HERE IS DECORATIVE-BUT-MEANINGFUL-LOOKING. The rotation speed is
   constant and carries no data, deliberately — a moving thing that encodes
   nothing is honest only while nobody reads meaning into it, so it is stated
   here and kept constant rather than tied to a metric it would misrepresent.

   ── PERFORMANCE BUDGET (a lesson paid for in a prototype) ────────────────
   • NO per-rung `ctx.filter = "blur(...)"`. Canvas filters are re-rasterised
     per call; at ~120 rungs × 60fps that is 7,200 blur passes a second and it
     visibly stalled. Glow is faked with 3 concentric strokes of decreasing
     alpha — same read, ~zero cost.
   • NO per-frame noise/grain pattern. There is no grain at all; if one is ever
     wanted it must be rendered ONCE to an offscreen canvas and blitted.
   • Depth is faked with alpha + scale, never blur.
   • IntersectionObserver stops the rAF loop when the canvas leaves the
     viewport; `visibilitychange` stops it when the tab is backgrounded. Both
     are required — a backgrounded tab still reports the element as intersecting.
   ============================================================================ */
(function (root) {
  "use strict";

  /* The five gate instruments + the two honest non-instrument states.

     ⚠️ TWO PALETTES, BECAUSE THE PAGE HAS TWO THEMES. Canvas cannot inherit a
     CSS custom property, so these are literals — which means they must be
     chosen per theme or one of them is wrong. Caught in review: the dark
     `corroboration` cream (255,232,180) is very nearly invisible on the light
     surface, and corroboration is the single most important state the helix
     shows. A palette that hides the thing the product exists to detect is not
     a styling nitpick. `theme.js` writes `data-theme="light"` on <html> (and
     removes it for dark), so that attribute is the source of truth. */
  var PALETTE = {
    dark: {
      congressional: [200, 150, 100],   /* --accent          */
      insider:       [ 78, 190, 150],   /* --direction-buy   */
      contracts:     [123, 163, 212],   /* --link            */
      earnings:      [232, 139,  74],   /* --severity-high   */
      institutional: [176, 132, 214],
      corroboration: [255, 232, 180],   /* the gate itself — bright on near-black */
      /* ⚠️ TWO INSTRUMENTS THAT ARE NOT LIVE TODAY BUT ARE NAMED IN THE MAP.
         `senate-lda` (RULE_09) and `fed-register` (RULE_08) are currently
         excluded, so they never arrive — but `CLAUDE.md` explicitly plans
         `fed-register`'s return under real issuer attribution, and an eligible
         instrument falling through to the `unknown` grey would render as "we
         don't know" the day it came back. Colour them now. Caught by the
         verifier. */
      "senate-lda":   [212, 176, 120],
      "fed-register": [140, 168, 190],
      context:       [ 90,  90,  98],   /* excluded — cannot corroborate */
      /* Deliberately much darker than `context`: "we could not categorise this"
         must not look like "this is context". They were near-identical. */
      unknown:       [ 48,  48,  56],
      strand:        [140, 140, 152]
    },
    light: {
      congressional: [150,  98,  40],
      insider:       [ 22, 132,  96],
      contracts:     [ 52,  96, 158],
      earnings:      [186,  86,  20],
      institutional: [118,  64, 164],
      corroboration: [146,  92,   8],   /* deep amber — legible on the light surface */
      "senate-lda":   [138, 104,  36],
      "fed-register": [ 60, 104, 134],
      context:       [126, 126, 134],
      unknown:       [188, 188, 194],
      strand:        [120, 120, 132]
    }
  };

  function themeName() {
    try {
      return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    } catch (_) { return "dark"; }
  }
  function palette() { return PALETTE[themeName()]; }

  /* Callers (the legend) ask for a colour rather than reading a frozen map, so
     they resolve against whatever theme is active at call time. */
  function colorFor(category) {
    var p = palette();
    return p[category] || p.unknown;
  }

  var SEVERITY_WIDTH = { LOW: 1.1, MEDIUM: 1.8, HIGH: 2.6, CRITICAL: 3.4 };

  var MAX_AGE_MS = 48 * 3600 * 1000;   /* matches the bus's 2-day lookback */
  var AGE_FLOOR  = 0.18;               /* an old rung dims, never vanishes */
  var MAX_RUNGS  = 140;

  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }
  function rgba(c, a) { return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a.toFixed(3) + ")"; }

  function mount(canvas, options) {
    if (!canvas || !canvas.getContext) return null;
    var opt = options || {};
    var ctx = canvas.getContext("2d");
    var rungs = [];               /* oldest → newest, left → right */
    var running = false;
    var rafId = null;
    var onScreen = true;
    var visible = (typeof document === "undefined") || document.visibilityState !== "hidden";
    var phase = 0;
    var lastTs = 0;

    /* frame instrumentation — so "it's fast" is measured, not asserted */
    var stats = { frames: 0, totalMs: 0, worstMs: 0, since: Date.now(), skipped: 0 };

    var dpr = Math.min(root.devicePixelRatio || 1, 2);   /* cap: 3x costs 2.25x fill for no visible gain */
    var W = 0, H = 0;

    function resize() {
      var r = canvas.getBoundingClientRect();
      W = Math.max(1, Math.round(r.width));
      H = Math.max(1, Math.round(r.height));
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function ageAlpha(evt, now) {
      var age = now - evt.timestamp;
      if (!isFinite(age) || age < 0) age = 0;
      var t = clamp(1 - (age / MAX_AGE_MS), 0, 1);
      return AGE_FLOOR + (1 - AGE_FLOOR) * t;
    }

    function draw(now) {
      var t0 = (root.performance && performance.now) ? performance.now() : 0;
      ctx.clearRect(0, 0, W, H);

      var n = rungs.length;
      if (!n) {
        drawEmpty();
      } else {
        var midY = H / 2;
        var amp = Math.min(H * 0.34, 90);
        var padX = 18;
        var span = Math.max(1, W - padX * 2);
        var step = span / Math.max(1, MAX_RUNGS - 1);
        var turns = 2.15;          /* helical turns across the full width */

        /* Draw far-side rungs first so near ones overlap them — depth ordering
           without a z-buffer and without blur. */
        var order = [];
        for (var i = 0; i < n; i++) {
          var x = padX + (i + (MAX_RUNGS - n)) * step;
          var a = phase + (i / Math.max(1, MAX_RUNGS - 1)) * Math.PI * 2 * turns;
          order.push({ i: i, x: x, a: a, z: Math.cos(a) });
        }
        order.sort(function (p, q) { return p.z - q.z; });

        /* backbones, drawn as two polylines in the same pass */
        strand(order, +1);
        strand(order, -1);

        for (var k = 0; k < order.length; k++) {
          drawRung(order[k], midY, amp, now);
        }
      }

      var t1 = (root.performance && performance.now) ? performance.now() : 0;
      stats.frames++;
      var ms = t1 - t0;
      stats.totalMs += ms;
      if (ms > stats.worstMs) stats.worstMs = ms;
    }

    function strand(order, sign) {
      var midY = H / 2, amp = Math.min(H * 0.34, 90);
      ctx.beginPath();
      var started = false;
      /* re-sort by x for a continuous line */
      var byX = order.slice().sort(function (p, q) { return p.x - q.x; });
      for (var i = 0; i < byX.length; i++) {
        var y = midY + Math.sin(byX[i].a) * amp * sign;
        if (!started) { ctx.moveTo(byX[i].x, y); started = true; }
        else ctx.lineTo(byX[i].x, y);
      }
      var sc = palette().strand;
      ctx.strokeStyle = "rgba(" + sc[0] + "," + sc[1] + "," + sc[2] + ",0.20)";
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    function drawRung(p, midY, amp, now) {
      var evt = rungs[p.i];
      var color = colorFor(evt.category);
      var base = ageAlpha(evt, now);

      /* DEPTH WITHOUT BLUR: z ∈ [-1,1] → alpha 0.35…1 and a slight width scale.
         The eye reads the same recession a blur would give, at no filter cost. */
      var depth = (p.z + 1) / 2;                 /* 0 = far, 1 = near */
      var depthA = 0.35 + 0.65 * depth;
      var alpha = clamp(base * depthA, 0, 1);

      var y1 = midY + Math.sin(p.a) * amp;
      var y2 = midY - Math.sin(p.a) * amp;

      var w = (SEVERITY_WIDTH[evt.severity] || SEVERITY_WIDTH.LOW) * (0.8 + 0.4 * depth);
      var corr = evt.corroboration && evt.corroboration.is_corroborated;

      if (corr) {
        /* GLOW = layered concentric strokes, NOT ctx.filter.
           ⚠️ AN ABSENT COUNT MUST NOT LOOK LIKE A REAL 3. It previously did
           (`instrument_count || 3`), so a corroborated row carrying no count
           rendered identically to a measured 3-instrument convergence — the
           verifier showed the two produced byte-identical strokes. Unknown now
           renders at the FLOOR, below any measured count, so "corroborated,
           breadth unknown" reads as less than "corroborated, 3 instruments".
           ⚠️ Also note it SATURATES: the gate's own tiers saturate at 5
           instruments, and so does this — 5 and 9 look the same, matching the
           scoring model rather than implying a resolution it does not have. */
        var ic = evt.corroboration.instrument_count;
        var strength = (typeof ic === "number" && isFinite(ic))
          ? clamp((ic - 2) / 3, 0.34, 1)      /* 3 -> 0.34, 4 -> 0.67, 5+ -> 1 */
          : 0.18;                             /* unknown breadth — below any measured count */
        var halo = [
          { mul: 6.0, a: 0.045 * strength },
          { mul: 3.4, a: 0.10  * strength },
          { mul: 1.9, a: 0.20  * strength }
        ];
        for (var h = 0; h < halo.length; h++) {
          ctx.beginPath();
          ctx.moveTo(p.x, y1); ctx.lineTo(p.x, y2);
          ctx.strokeStyle = rgba(color, alpha * halo[h].a);
          ctx.lineWidth = w * halo[h].mul;
          ctx.lineCap = "round";
          ctx.stroke();
        }
      }

      ctx.beginPath();
      ctx.moveTo(p.x, y1); ctx.lineTo(p.x, y2);
      ctx.strokeStyle = rgba(color, alpha * (corr ? 0.98 : 0.72));
      ctx.lineWidth = w;
      ctx.lineCap = "round";
      ctx.stroke();

      /* ENDPOINTS. Corroborated rungs get brighter, larger caps — the single
         cheapest way to make "the gate agreed" readable at a glance. */
      var r = corr ? (w * 1.25 + 1.4) : (w * 0.62 + 0.5);
      var capA = clamp(alpha * (corr ? 1 : 0.55), 0, 1);
      ctx.fillStyle = corr
        ? rgba([Math.min(255, color[0] + 55), Math.min(255, color[1] + 55), Math.min(255, color[2] + 55)], capA)
        : rgba(color, capA);
      ctx.beginPath(); ctx.arc(p.x, y1, r, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.arc(p.x, y2, r, 0, Math.PI * 2); ctx.fill();
    }

    function drawEmpty() {
      ctx.strokeStyle = "rgba(120,120,132,0.16)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(18, H / 2); ctx.lineTo(W - 18, H / 2);
      ctx.stroke();
    }

    function frame(ts) {
      if (!running) return;
      if (!lastTs) lastTs = ts;
      var dt = Math.min(ts - lastTs, 100);      /* clamp: a long stall must not spin the helix */
      lastTs = ts;
      phase += dt * 0.00035;                    /* constant — encodes nothing */
      draw(Date.now());
      rafId = root.requestAnimationFrame(frame);
    }

    function shouldRun() { return onScreen && visible; }

    function sync() {
      if (shouldRun() && !running) {
        running = true; lastTs = 0;
        rafId = root.requestAnimationFrame(frame);
      } else if (!shouldRun() && running) {
        running = false;
        if (rafId) { root.cancelAnimationFrame(rafId); rafId = null; }
        stats.skipped++;
      }
    }

    /* ── the two pause conditions, both genuinely wired ─────────────────── */
    var io = null;
    if (typeof IntersectionObserver === "function") {
      io = new IntersectionObserver(function (entries) {
        for (var i = 0; i < entries.length; i++) onScreen = entries[i].isIntersecting;
        sync();
      }, { threshold: 0 });
      io.observe(canvas);
    }
    function onVis() { visible = document.visibilityState !== "hidden"; sync(); }
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVis);

    /* The palette is resolved at draw time, so a RUNNING canvas re-colours on
       its next frame for free. A PAUSED one would sit in the old theme until
       something else woke it, so repaint it explicitly. `theme.js:62` is the
       event; listening to it beats polling the attribute. */
    function onTheme() { if (!running) draw(Date.now()); }
    if (typeof document !== "undefined") document.addEventListener("scope:themechange", onTheme);

    var ro = null;
    if (typeof ResizeObserver === "function") {
      ro = new ResizeObserver(function () { resize(); if (!running) draw(Date.now()); });
      ro.observe(canvas);
    } else {
      /* Legacy path. `destroy()` must remove this too — it previously did not,
         which leaked a listener per mount on browsers without ResizeObserver. */
      root.addEventListener("resize", resize);
    }

    function push(evt) {
      if (!evt) return;
      rungs.push(evt);
      if (rungs.length > MAX_RUNGS) rungs.splice(0, rungs.length - MAX_RUNGS);
      if (!running) draw(Date.now());          /* keep a paused helix truthful */
      if (typeof opt.onChange === "function") opt.onChange(rungs.length);
    }

    resize();
    draw(Date.now());
    sync();

    var unsubscribe = function () {};
    if (root.SignalEvents) {
      root.SignalEvents.replay(push);
      unsubscribe = root.SignalEvents.subscribe(push);
    }

    return {
      push: push,
      count: function () { return rungs.length; },
      rungs: function () { return rungs.slice(); },
      isRunning: function () { return running; },
      isOnScreen: function () { return onScreen; },
      isTabVisible: function () { return visible; },
      perf: function () {
        return {
          frames: stats.frames,
          avgMs: stats.frames ? +(stats.totalMs / stats.frames).toFixed(3) : 0,
          worstMs: +stats.worstMs.toFixed(3),
          pauses: stats.skipped,
          budgetMs: 16.67,
          rungs: rungs.length
        };
      },
      resetPerf: function () { stats = { frames: 0, totalMs: 0, worstMs: 0, since: Date.now(), skipped: stats.skipped }; },
      destroy: function () {
        running = false;
        if (rafId) root.cancelAnimationFrame(rafId);
        if (io) io.disconnect();
        if (ro) ro.disconnect(); else root.removeEventListener("resize", resize);
        if (typeof document !== "undefined") {
          document.removeEventListener("visibilitychange", onVis);
          document.removeEventListener("scope:themechange", onTheme);
        }
        unsubscribe();
      }
    };
  }

  root.SignalDNA = { mount: mount, colorFor: colorFor, PALETTE: PALETTE,
                     theme: themeName, SEVERITY_WIDTH: SEVERITY_WIDTH };
})(window);
