/* ============================================================================
   MARKET PULSE — system state from REAL alert frequency.

   Subscribes to `SignalEvents`. The beat rate and the state label are computed
   from how often Scope actually emitted alerts inside a rolling window. There
   is no generator and no idle animation that implies activity: at zero events
   the pulse is flat and reads "quiet".

   ── THE WINDOW, AND WHY IT IS 60 MINUTES ─────────────────────────────────
   ⚠️ The sandbox prototype used a 12-SECOND window. That was sized for a fake
   generator firing every few seconds and is meaningless against real Scope
   cadence — it would read "quiet" essentially 100% of the time and the whole
   indicator would be decorative.

   ⚠️ AND THE FIRST CALIBRATION WAS MEASURED ON THE WRONG POPULATION. It used a
   `rule=ALL` firehose sample (median 5/hr), but the bus feeds the endpoint's
   DEFAULT view, which excludes RULE_07 / RULE_OSINT / RULE_REDDIT. A threshold
   ladder calibrated on ~3× the volume the component actually sees is a
   decorative ladder. Flagged by an independent verifier for having an unstated
   basis; re-measured against the right population:

   Production, 500 alerts on the DEFAULT view, 2026-08-07 03:32 → 08-12 12:09
   (62 hours with any activity):

       alerts/hour   min 1   p25 1   median 3   p75 8   p90 25   max 63

   A window has to be long enough that a typical hour contains several events
   (or the state never leaves "quiet") and short enough that a burst still moves
   it within its own hour. 60 minutes satisfies both, and the thresholds are the
   quartiles of that distribution rather than round numbers:

       quiet         0–1 /hr    <= p25
       active        2–7 /hr    straddles the median of 3
       elevated      8–24 /hr   p75 .. below p90
       high-signal   25+ /hr    >= p90  (observed 25 … 63)

   ⭐ The ladder itself survived the correction — it lands on p25 / median / p75 /
   p90 of the correct population. What was wrong was the stated basis, and a
   number nobody could have checked is not a justification.

   ⚠️ These are calibrated to TODAY's cadence on TODAY's default view. Re-measure
   if the rule set, the exclusions, or the bus's `allRules` default changes. They
   are constants with a provenance, not magic numbers.
   ============================================================================ */
(function (root) {
  "use strict";

  var WINDOW_MS = 60 * 60 * 1000;

  /* ⚠️ Two palettes, same reason as the DNA: canvas cannot read a CSS custom
     property, so a single literal set would be wrong on one of the two themes.
     `theme.js` writes `data-theme="light"` on <html>; that is the source of
     truth, resolved at draw time so the toggle takes effect without a reload. */
  var STATES = [
    { id: "quiet",       label: "Quiet",       min: 0,  bpm: 34,
      color: [122, 122, 134], colorLight: [110, 110, 122] },
    { id: "active",      label: "Active",      min: 2,  bpm: 54,
      color: [200, 150, 100], colorLight: [150,  98,  40] },
    { id: "elevated",    label: "Elevated",    min: 8,  bpm: 78,
      color: [232, 139,  74], colorLight: [186,  86,  20] },
    { id: "high-signal", label: "High signal", min: 25, bpm: 104,
      color: [224, 104, 104], colorLight: [176,  42,  42] }
  ];

  function isLight() {
    try { return document.documentElement.getAttribute("data-theme") === "light"; }
    catch (_) { return false; }
  }
  function colorOf(st) { return isLight() ? st.colorLight : st.color; }

  /* A corroboration is the rarest thing Scope produces — one in the entire
     history of the system at time of writing. The spike is therefore held long
     enough to actually be seen. */
  var SPIKE_MS = 6000;

  function stateFor(rate) {
    var s = STATES[0];
    for (var i = 0; i < STATES.length; i++) if (rate >= STATES[i].min) s = STATES[i];
    return s;
  }

  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

  function mount(canvas, options) {
    if (!canvas || !canvas.getContext) return null;
    var opt = options || {};
    var ctx = canvas.getContext("2d");

    var times = [];               /* event timestamps inside the window */
    var spikeUntil = 0;
    var lastSpikeEvent = null;
    var beatPhase = 0;
    var running = false, rafId = null, lastTs = 0;
    var onScreen = true;
    var visible = (typeof document === "undefined") || document.visibilityState !== "hidden";
    var stats = { frames: 0, totalMs: 0, worstMs: 0, pauses: 0 };

    var dpr = Math.min(root.devicePixelRatio || 1, 2);
    var W = 0, H = 0;

    function resize() {
      var r = canvas.getBoundingClientRect();
      W = Math.max(1, Math.round(r.width));
      H = Math.max(1, Math.round(r.height));
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function prune(now) {
      var cut = now - WINDOW_MS;
      while (times.length && times[0] < cut) times.shift();
    }

    /* ── the only public truth of this component ─────────────────────────── */
    function read(now) {
      now = now || Date.now();
      prune(now);
      var rate = times.length;                    /* events per WINDOW_MS == per hour */
      var st = stateFor(rate);
      return {
        state: st.id,
        label: st.label,
        rate: rate,
        windowMs: WINDOW_MS,
        windowLabel: "60 min",
        bpm: st.bpm,
        spiking: now < spikeUntil,
        lastCorroboration: lastSpikeEvent
          ? { event_id: lastSpikeEvent.event_id, ticker: lastSpikeEvent.ticker,
              instrument_count: lastSpikeEvent.corroboration.instrument_count }
          : null
      };
    }

    function draw(now) {
      var t0 = (root.performance && performance.now) ? performance.now() : 0;
      var r = read(now);
      ctx.clearRect(0, 0, W, H);

      var st = stateFor(r.rate);
      var col = colorOf(st);
      var mid = H / 2;
      var spiking = r.spiking;

      /* baseline */
      ctx.strokeStyle = isLight() ? "rgba(90,90,100,0.22)" : "rgba(120,120,132,0.18)";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(W, mid); ctx.stroke();

      /* One travelling ECG-style beat. Its RATE is the datum; the shape is not.
         At `quiet` with zero events the amplitude collapses to a flat line —
         an idle system must not look alive. */
      var amp = (r.rate === 0) ? 0 : clamp(H * 0.16 + H * 0.03 * Math.log(1 + r.rate), 4, H * 0.42);
      if (spiking) amp = Math.min(H * 0.46, amp * 1.9);

      ctx.beginPath();
      for (var x = 0; x <= W; x += 2) {
        var u = (x / W) + beatPhase;
        var y = mid - pulseWave(u) * amp;
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      /* Glow by layered strokes — same rule as the DNA: no ctx.filter anywhere. */
      var a = spiking ? 1 : 0.9;
      ctx.strokeStyle = "rgba(" + col[0] + "," + col[1] + "," + col[2] + "," + (a * 0.10) + ")";
      ctx.lineWidth = 7; ctx.stroke();
      ctx.strokeStyle = "rgba(" + col[0] + "," + col[1] + "," + col[2] + "," + (a * 0.22) + ")";
      ctx.lineWidth = 3.4; ctx.stroke();
      ctx.strokeStyle = "rgba(" + col[0] + "," + col[1] + "," + col[2] + "," + a + ")";
      ctx.lineWidth = 1.5; ctx.stroke();

      var t1 = (root.performance && performance.now) ? performance.now() : 0;
      stats.frames++;
      var ms = t1 - t0;
      stats.totalMs += ms;
      if (ms > stats.worstMs) stats.worstMs = ms;

      if (typeof opt.onRead === "function") opt.onRead(r);
    }

    function pulseWave(u) {
      /* periodic QRS-ish bump: narrow spike + small rebound, zero elsewhere */
      var f = u - Math.floor(u);
      if (f < 0.42 || f > 0.62) return 0;
      var k = (f - 0.42) / 0.20;                 /* 0..1 across the beat */
      return Math.sin(k * Math.PI) * (k < 0.45 ? 1 : 0.55);
    }

    function frame(ts) {
      if (!running) return;
      if (!lastTs) lastTs = ts;
      var dt = Math.min(ts - lastTs, 100);
      lastTs = ts;
      var r = read();
      /* bpm → cycles per second → phase per ms. THIS is where real frequency
         becomes visible motion. */
      beatPhase += (dt / 1000) * (r.bpm / 60) * (r.spiking ? 1.6 : 1);
      draw(Date.now());
      rafId = root.requestAnimationFrame(frame);
    }

    function shouldRun() { return onScreen && visible; }
    function sync() {
      if (shouldRun() && !running) { running = true; lastTs = 0; rafId = root.requestAnimationFrame(frame); }
      else if (!shouldRun() && running) {
        running = false;
        if (rafId) { root.cancelAnimationFrame(rafId); rafId = null; }
        stats.pauses++;
      }
    }

    var io = null;
    if (typeof IntersectionObserver === "function") {
      io = new IntersectionObserver(function (es) {
        for (var i = 0; i < es.length; i++) onScreen = es[i].isIntersecting;
        sync();
      }, { threshold: 0 });
      io.observe(canvas);
    }
    function onVis() { visible = document.visibilityState !== "hidden"; sync(); }
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVis);
    /* See the DNA's note: a paused canvas needs an explicit repaint on a theme flip. */
    function onTheme() { if (!running) draw(Date.now()); }
    if (typeof document !== "undefined") document.addEventListener("scope:themechange", onTheme);

    var ro = null;
    if (typeof ResizeObserver === "function") {
      ro = new ResizeObserver(function () { resize(); if (!running) draw(Date.now()); });
      ro.observe(canvas);
    } else { root.addEventListener("resize", resize); }   /* removed in destroy() */

    function push(evt) {
      if (!evt || !isFinite(evt.timestamp)) return;
      times.push(evt.timestamp);
      times.sort(function (x, y) { return x - y; });     /* seed batches can arrive out of order */

      /* ⚠️ THE SPIKE FIRES ON CORROBORATION ONLY. Not on CRITICAL, not on a
         burst — on the gate having actually agreed. Anything looser would make
         the rarest event in the system indistinguishable from a noisy minute. */
      if (evt.corroboration && evt.corroboration.is_corroborated === true) {
        spikeUntil = Date.now() + SPIKE_MS;
        lastSpikeEvent = evt;
        if (typeof opt.onCorroboration === "function") opt.onCorroboration(evt);
      }
      if (!running) draw(Date.now());
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
      read: read,
      windowMs: WINDOW_MS,
      thresholds: STATES.map(function (s) { return { id: s.id, min: s.min }; }),
      isRunning: function () { return running; },
      isOnScreen: function () { return onScreen; },
      isTabVisible: function () { return visible; },
      perf: function () {
        return {
          frames: stats.frames,
          avgMs: stats.frames ? +(stats.totalMs / stats.frames).toFixed(3) : 0,
          worstMs: +stats.worstMs.toFixed(3),
          pauses: stats.pauses, budgetMs: 16.67
        };
      },
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

  root.SignalPulse = { mount: mount, WINDOW_MS: WINDOW_MS, STATES: STATES, colorOf: colorOf };
})(window);
