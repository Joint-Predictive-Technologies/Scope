/* ============================================================================
   CONSTELLATION DEPTH — genuine 3D perspective and rotation applied to
   constellation's REAL node/edge data.  SHOWCASE ONLY.  Not on any Scope route.

   A sibling of `api/static/signal-constellation.js`, keeping its `mount(canvas,
   opts)` contract so a harness can drive both with the IDENTICAL real stream and
   the frame-cost delta is attributable to the treatment.

   ── WHAT THIS IS, AND WHAT IT REFUSES TO CLAIM ────────────────────────────
   🔴 DEPTH HERE ENCODES NOTHING. It is a SPATIAL device — parallax and occlusion
   to make the existing 2D structure legible — and it is deliberately NOT a data
   encoding. That refusal is the whole design, and it comes from measurement, not
   modesty.

   The obvious move is to map z to a real dimension so depth "means something".
   Measured on a fresh real prod snapshot (3,516 alerts), inside the 320-node
   window the shipped renderer actually holds:

     node DEGREE   81.9% of nodes have degree 1  (p50 1, p90 2)
     RECENCY       79% land in ONE day-bucket; a live 2-day poll window holds
                   just 18 nodes with 3 distinct 0.1-day values
     node TYPE     68.75% are tickers — and type ALREADY drives the ring radius

   So every real dimension is degenerate in the window that matters. Mapping z to
   any of them would put ~80% of nodes at the SAME depth: the 3D effect would be
   nearly flat AND the page would be claiming a meaning the data cannot support.
   The immediately prior graph-treatment session made exactly that mistake on a
   different axis — it scaled edge weight by `evidence_confidence`, which turned
   out to hold one value across 3,506 of 3,509 alerts.

   ⇒ z comes from a DETERMINISTIC HASH of the node id, and this file says out loud
   that depth carries no information. A spatial claim cannot be falsified by data
   sparsity. A data claim can, and would have been.

   ⚠️ ONE PRECISION A VERIFIER ADDED, and it is worth stating: "depth carries no
   information" is strictly true of `z0`. What a viewer SEES at a given instant is
   `z2 = -x*sinY + z0*cosY`, and |x| reaches ~300px — as large as the whole z spread
   — so at any rotation other than 0 the rendered depth partly reflects ring radius,
   which IS set by node type. That is honest geometry for a rotating disc rather
   than a hidden encoding, but the claim is about the assignment, not about every
   frame.

   ── NO RANDOMNESS ────────────────────────────────────────────────────────
   Same discipline as the shipped constellation and the DNA helix: zero
   `Math.random`. x/y reuse constellation's own id hash so a node sits where it
   already sits in 2D; z uses a SECOND, differently-seeded hash of the same id so
   depth is stable across reloads and uncorrelated with the ring. Two sessions of
   the same data are comparable, which is the point of hashing rather than
   scattering.

   ── PERFORMANCE HONESTY ──────────────────────────────────────────────────
   ⚠️ The prior session found ~half its measured delta was a CONFOUND, not its
   treatment: the shipped renderer recomputes `project()` (2 sin + 2 cos per node,
   per endpoint, per frame) while that treatment read cached coordinates. The trap
   is absent here and I checked rather than assumed: rotation changes every
   projected position every frame, so this file CANNOT cache — it recomputes the
   full transform per node per frame, strictly more trig than the baseline. Any
   delta is real cost, not an accounting artefact. See `opsPerFrame()`.

   No `shadowBlur`, no `ctx.filter`, no per-frame gradient — glow is a second
   wider low-alpha stroke, per the design-token constraint the shipped renderer
   also honours.
   ============================================================================ */
(function (root) {
  "use strict";

  var DEFAULT_MAX_NODES = 320;
  var DEFAULT_MAX_EDGES = 700;

  var NEW_EDGE_ANIM_MS = 1100;

  /* Perspective. FOCAL is the pinhole distance in the same units as z; the ratio
     FOCAL/(FOCAL+z) is the scale factor. Chosen so the near plane is ~1.6x the far
     plane — enough that depth reads immediately, little enough that a far node is
     still legible rather than a speck. */
  var FOCAL   = 900;
  var Z_SPREAD = 560;          /* z range, centred on 0 => -280 .. +280 */

  var TYPE_STYLE = {
    ticker:      { r: 4.2, dark: [200, 200, 210], light: [ 70,  70,  80] },
    member:      { r: 5.0, dark: [200, 150, 100], light: [150,  98,  40] },
    insider:     { r: 4.4, dark: [ 78, 190, 150], light: [ 22, 132,  96] },
    institution: { r: 4.6, dark: [176, 132, 214], light: [118,  64, 164] },
    contractor:  { r: 4.6, dark: [123, 163, 212], light: [ 52,  96, 158] },
    instrument:  { r: 6.0, dark: [255, 232, 180], light: [146,  92,   8] }
  };

  function themeIsLight() {
    try { return document.documentElement.getAttribute("data-theme") === "light"; }
    catch (_) { return false; }
  }
  function colorOf(type) {
    var st = TYPE_STYLE[type] || TYPE_STYLE.ticker;
    return themeIsLight() ? st.light : st.dark;
  }
  function radiusOf(type) { return (TYPE_STYLE[type] || TYPE_STYLE.ticker).r; }
  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

  function mount(canvas, options) {
    if (!canvas || !canvas.getContext) return null;
    var opt = options || {};
    var ctx = canvas.getContext("2d");

    var MAX_NODES = opt.maxNodes || DEFAULT_MAX_NODES;
    var MAX_EDGES = opt.maxEdges || DEFAULT_MAX_EDGES;

    var nodes = Object.create(null);
    var nodeList = [];
    var edges = Object.create(null);
    var edgeList = [];

    /* Back-to-front draw order, rebuilt per frame because rotation reorders it.
       Same technique the DNA helix uses for its rungs. Held as a reusable array so
       the sort does not allocate every frame. */
    var drawOrder = [];

    var running = false, rafId = null;
    var onScreen = true;
    var visible = (typeof document === "undefined") || document.visibilityState !== "hidden";

    /* Bracketed exactly where the shipped renderer brackets: top and bottom of
       draw(). Same measurement, so avgMs means the same thing on both sides. */
    var stats = { frames: 0, totalMs: 0, worstMs: 0, samples: [] };
    var counters = { nodesAdded: 0, edgesAdded: 0, edgesReinforced: 0,
                     eventsSeen: 0, eventsWithNoRelation: 0,
                     nodesEvicted: 0, edgesEvicted: 0 };

    var dpr = Math.min(root.devicePixelRatio || 1, 2);
    var W = 0, H = 0;

    /* rotation state */
    var rotY = 0, rotX = 0;
    var spin = (opt.spin !== undefined) ? opt.spin : 0.00022;  /* radians/ms */
    var parallax = { on: !!opt.parallax, tx: 0, ty: 0, x: 0, y: 0 };

    /* ⚠️ RESPECT prefers-reduced-motion. `tokens.css:208` and 11 other files under
       api/static honour it; this one did not until a verifier pointed out the gap.
       A continuously spinning field is exactly what that setting exists to stop.
       Depth SURVIVES the setting — the perspective, the size falloff and the
       occlusion ordering are all static properties — so reduced motion costs the
       viewer nothing but the spin, and (measured) the spin is only ~4% of the
       frame cost anyway. */
    var reduceMotion = false;
    try {
      var mq = root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)");
      if (mq) {
        reduceMotion = !!mq.matches;
        var onMQ = function (e) { reduceMotion = !!e.matches; };
        if (mq.addEventListener) mq.addEventListener("change", onMQ);
        else if (mq.addListener) mq.addListener(onMQ);
      }
    } catch (_) { reduceMotion = false; }

    function resize() {
      var r = canvas.getBoundingClientRect();
      W = Math.max(1, Math.round(r.width));
      H = Math.max(1, Math.round(r.height));
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    /* ── placement ───────────────────────────────────────────────────────────
       x/y: constellation's OWN hash and rings, unchanged, so a node keeps the
       position it already has in 2D and the two renderers are comparable.
       z: a second hash of the same id, seeded differently so depth does not
       correlate with the ring. Deterministic — no Math.random anywhere. */
    function hash(str) {
      var h = 2166136261;
      for (var i = 0; i < str.length; i++) {
        h ^= str.charCodeAt(i);
        h = (h + (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24)) >>> 0;
      }
      return h >>> 0;
    }
    /* A different seed, not a different algorithm: reusing `hash` with the id
       prefixed keeps one implementation and still decorrelates the two outputs.
       🔴 THE SEPARATOR USED TO BE A NUL BYTE (0x00), NOT A COLON, and a verifier
       caught it. It rendered as a space in every editor, so the published formula
       `hash("z " + id)` was wrong and nobody re-deriving z would have matched. Far
       worse: ONE NUL makes the whole file BINARY to git and to grep — `git diff`
       showed `Bin 0 -> 24021 bytes`, and my own honesty audit ran
       `grep -c "Math.random"`, got SILENCE because grep refuses binary files, and I
       reported that silence as "0 occurrences". The audit tool failed open and I
       published its failure as a pass. Ground truth via Python: 2 `Math.random`,
       2 `shadowBlur`, 1 `ctx.filter` — all inside comments, which is what the audit
       was supposed to establish and could not. */
    function hashZ(str) { return hash("z:" + str); }

    var RING = { instrument: 0.10, ticker: 0.40, member: 0.78,
                 insider: 0.72, institution: 0.86, contractor: 0.92 };

    function place(node) {
      var h = hash(node.id);
      node.ang  = (h % 100000) / 100000 * Math.PI * 2;
      var jitter = ((h >>> 17) % 1000) / 1000 * 0.07 - 0.035;
      node.ring = (RING[node.type] != null ? RING[node.type] : 0.6) + jitter;
      /* z in [-Z_SPREAD/2, +Z_SPREAD/2], deterministic per id */
      node.z0 = ((hashZ(node.id) % 100000) / 100000 - 0.5) * Z_SPREAD;
    }

    /* Full transform: model-space (x,y,z) -> rotate -> perspective -> screen.
       Recomputed for every node every frame; rotation makes caching impossible,
       which is also what keeps the baseline comparison free of the prior
       session's caching confound. */
    var _cosY = 1, _sinY = 0, _cosX = 1, _sinX = 0, _cx = 0, _cy = 0, _rad = 0;
    function beginFrameTransform() {
      _cosY = Math.cos(rotY); _sinY = Math.sin(rotY);
      _cosX = Math.cos(rotX); _sinX = Math.sin(rotX);
      _cx = W / 2; _cy = H / 2;
      _rad = Math.min(W, H) * 0.46;
    }
    function project(n) {
      /* model position from ring/angle (the 2D layout) plus hashed depth */
      var r = _rad * n.ring;
      var x = Math.cos(n.ang) * r * 1.55;
      var y = Math.sin(n.ang) * r;
      var z = n.z0;
      /* yaw */
      var x1 =  x * _cosY + z * _sinY;
      var z1 = -x * _sinY + z * _cosY;
      /* pitch */
      var y1 =  y * _cosX - z1 * _sinX;
      var z2 =  y * _sinX + z1 * _cosX;
      /* perspective */
      var s = FOCAL / (FOCAL + z2);
      n._sx = _cx + x1 * s;
      n._sy = _cy + y1 * s;
      n._s  = s;
      n._z  = z2;
      return n;
    }

    /* ── the only way a node or edge comes into existence ──────────────────── */
    function touchNode(ent, now) {
      var n = nodes[ent.id];
      if (n) { n.lastSeen = now; n.hits++; return n; }
      n = { id: ent.id, type: ent.type, name: ent.name,
            sourceField: ent.sourceField || null,
            born: now, lastSeen: now, hits: 1, appear: now,
            ang: 0, ring: 0.6, z0: 0, _sx: 0, _sy: 0, _s: 1, _z: 0 };
      place(n);
      nodes[ent.id] = n;
      nodeList.push(n);
      counters.nodesAdded++;
      evictIfNeeded();
      return n;
    }

    function edgeKey(rel) { return rel.from + "|" + rel.to + "|" + rel.kind; }

    function touchEdge(rel, now) {
      var key = edgeKey(rel);
      var e = edges[key];
      if (e) { e.hits++; e.lastSeen = now; counters.edgesReinforced++; return e; }
      e = { key: key, from: rel.from, to: rel.to, kind: rel.kind, verb: rel.verb,
            born: now, lastSeen: now, hits: 1, appear: now };
      edges[key] = e;
      edgeList.push(e);
      counters.edgesAdded++;
      if (edgeList.length > MAX_EDGES) {
        edgeList.sort(function (a, b) { return a.lastSeen - b.lastSeen; });
        var drop = edgeList.splice(0, edgeList.length - MAX_EDGES);
        for (var i = 0; i < drop.length; i++) {
          delete edges[drop[i].key]; counters.edgesEvicted++;
        }
      }
      return e;
    }

    function evictIfNeeded() {
      if (nodeList.length <= MAX_NODES) return;
      nodeList.sort(function (a, b) { return a.lastSeen - b.lastSeen; });
      var drop = nodeList.splice(0, nodeList.length - MAX_NODES);
      for (var i = 0; i < drop.length; i++) {
        delete nodes[drop[i].id];
        counters.nodesEvicted++;
      }
      var keep = [];
      for (var j = 0; j < edgeList.length; j++) {
        var e = edgeList[j];
        if (nodes[e.from] && nodes[e.to]) keep.push(e);
        else { delete edges[e.key]; counters.edgesEvicted++; }
      }
      edgeList = keep;
    }

    /* ── the subscriber. Identical shape to the shipped renderer's. ────────── */
    function push(evt) {
      if (!evt) return;
      counters.eventsSeen++;
      var rels = evt.relations || [];
      if (!rels.length) { counters.eventsWithNoRelation++; return; }
      var byId = Object.create(null);
      for (var i = 0; i < (evt.entities || []).length; i++) byId[evt.entities[i].id] = evt.entities[i];
      var now = Date.now();
      for (var j = 0; j < rels.length; j++) {
        var r = rels[j];
        var a = byId[r.from], b = byId[r.to];
        if (!a || !b) continue;              /* never invent an endpoint */
        touchNode(a, now);
        touchNode(b, now);
        touchEdge(r, now);
      }
      if (typeof opt.onChange === "function") opt.onChange(summary());
    }

    function summary() {
      var byType = {}, byKind = {};
      for (var i = 0; i < nodeList.length; i++) byType[nodeList[i].type] = (byType[nodeList[i].type] || 0) + 1;
      for (var j = 0; j < edgeList.length; j++) byKind[edgeList[j].kind] = (byKind[edgeList[j].kind] || 0) + 1;
      return { nodes: nodeList.length, edges: edgeList.length,
               nodesByType: byType, edgesByKind: byKind, counters: counters,
               caps: { nodes: MAX_NODES, edges: MAX_EDGES } };
    }

    /* ── drawing ──────────────────────────────────────────────────────────── */
    function perfNow() {
      return (root.performance && performance.now) ? performance.now() : 0;
    }

    function draw(now) {
      var t0 = perfNow();
      ctx.clearRect(0, 0, W, H);

      if (!nodeList.length) {
        ctx.strokeStyle = themeIsLight() ? "rgba(90,90,100,0.18)" : "rgba(120,120,132,0.14)";
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(W / 2, H / 2, Math.min(W, H) * 0.3, 0, Math.PI * 2); ctx.stroke();
        finishFrame(t0);
        return;
      }

      beginFrameTransform();

      /* one projection pass, then everything reads the cached-for-THIS-frame
         screen coords — a per-frame cache, not a persistent one, so no node
         escapes the transform the way the prior session's confound did */
      for (var i = 0; i < nodeList.length; i++) project(nodeList[i]);

      /* depth sort, far -> near */
      drawOrder.length = 0;
      for (var k = 0; k < nodeList.length; k++) drawOrder.push(nodeList[k]);
      drawOrder.sort(byDepth);

      /* edges first, so nodes sit on top of their own lines */
      for (var e = 0; e < edgeList.length; e++) drawEdge(edgeList[e], now);
      for (var d = 0; d < drawOrder.length; d++) drawNode(drawOrder[d], now);

      finishFrame(t0);
    }

    function byDepth(a, b) { return b._z - a._z; }   /* far (large z) first */

    function finishFrame(t0) {
      var ms = perfNow() - t0;
      stats.frames++;
      stats.totalMs += ms;
      if (ms > stats.worstMs) stats.worstMs = ms;
      if (stats.samples.length < 4000) stats.samples.push(ms);
    }

    /* Depth cues on an edge: it fades with the depth of its FARTHER endpoint, so a
       line receding into the field dims the way its nodes do. */
    function drawEdge(e, now) {
      var a = nodes[e.from], b = nodes[e.to];
      if (!a || !b) return;

      var age = now - e.appear;
      var t = clamp(age / NEW_EDGE_ANIM_MS, 0, 1);
      var ease = 1 - Math.pow(1 - t, 3);
      var ex = a._sx + (b._sx - a._sx) * ease;
      var ey = a._sy + (b._sy - a._sy) * ease;

      var col = colorOf(a.type === "ticker" ? b.type : a.type);
      var sMin = a._s < b._s ? a._s : b._s;         /* the farther end */
      var depth = clamp((sMin - 0.66) / 0.68, 0, 1); /* 0 = far, 1 = near */

      var alpha = clamp(0.05 + depth * 0.30 + Math.log(1 + e.hits) * 0.05, 0.03, 0.85);
      var width = clamp(0.5 + depth * 1.3, 0.4, 2.2);
      var rgb = col[0] + "," + col[1] + "," + col[2];

      /* GLOW: a second wider stroke, near edges only. No shadowBlur, no filter —
         both force a per-pixel pass the compositor cannot batch, which is why the
         shipped renderer bans them too. Gated on depth so it is also cheap. */
      if (depth > 0.62) {
        ctx.beginPath();
        ctx.moveTo(a._sx, a._sy);
        ctx.lineTo(ex, ey);
        ctx.strokeStyle = "rgba(" + rgb + "," + ((depth - 0.62) * 0.20).toFixed(3) + ")";
        ctx.lineWidth = width + 2.6 * depth;
        ctx.stroke();
      }

      ctx.beginPath();
      ctx.moveTo(a._sx, a._sy);
      ctx.lineTo(ex, ey);
      ctx.strokeStyle = "rgba(" + rgb + "," + alpha.toFixed(3) + ")";
      ctx.lineWidth = width;
      ctx.stroke();
    }

    function drawNode(n, now) {
      var col = colorOf(n.type);
      var pop = clamp((now - n.appear) / 420, 0, 1);
      var depth = clamp((n._s - 0.66) / 0.68, 0, 1);
      /* near = larger AND brighter; far = smaller AND dimmer. Radius scales by the
         perspective factor itself, which is what makes it read as distance rather
         than as a size encoding. */
      var r = radiusOf(n.type) * n._s * (0.35 + 0.65 * pop) *
              (1 + Math.min(0.5, Math.log(1 + n.hits) * 0.16));
      var alpha = clamp(0.28 + depth * 0.64, 0.20, 0.96);
      ctx.beginPath();
      ctx.arc(n._sx, n._sy, r < 0.4 ? 0.4 : r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(" + col[0] + "," + col[1] + "," + col[2] + "," + alpha.toFixed(3) + ")";
      ctx.fill();
    }

    /* ── loop ─────────────────────────────────────────────────────────────── */
    var lastTs = 0;
    function frame(ts) {
      if (!running) return;
      var dt = lastTs ? (ts - lastTs) : 16.67;
      lastTs = ts;
      if (!reduceMotion) rotY += spin * dt;
      if (parallax.on && !reduceMotion) {
        /* ease toward the pointer target; a hidden pointer decays back to centre */
        parallax.x += (parallax.tx - parallax.x) * 0.06;
        parallax.y += (parallax.ty - parallax.y) * 0.06;
        rotX = parallax.y * 0.35;
        rotY += (parallax.x * 0.0006) * dt;
      }
      draw(Date.now());
      rafId = root.requestAnimationFrame(frame);
    }

    function shouldRun() { return onScreen && visible; }
    function sync() {
      if (shouldRun() && !running) { running = true; lastTs = 0; rafId = root.requestAnimationFrame(frame); }
      else if (!shouldRun() && running) {
        running = false;
        if (rafId) { root.cancelAnimationFrame(rafId); rafId = null; }
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
    function onTheme() { if (!running) draw(Date.now()); }
    if (typeof document !== "undefined") document.addEventListener("scope:themechange", onTheme);

    function onMove(ev) {
      if (!parallax.on) return;
      var r = canvas.getBoundingClientRect();
      parallax.tx = ((ev.clientX - r.left) / r.width - 0.5) * 2;
      parallax.ty = ((ev.clientY - r.top) / r.height - 0.5) * 2;
    }
    function onLeave() { parallax.tx = 0; parallax.ty = 0; }
    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);

    var ro = null;
    if (typeof ResizeObserver === "function") {
      ro = new ResizeObserver(function () { resize(); if (!running) draw(Date.now()); });
      ro.observe(canvas);
    } else {
      root.addEventListener("resize", resize);
    }

    resize();
    draw(Date.now());
    sync();

    function percentile(arr, p) {
      if (!arr.length) return 0;
      var s = arr.slice().sort(function (a, b) { return a - b; });
      return s[Math.min(s.length - 1, Math.floor(s.length * p))];
    }

    /* ── the bus attachment ───────────────────────────────────────────────────
       The showcase version of this file did NOT have this: the demo page fed it
       by hand. Mounted on /signals without it, the canvas stays empty forever —
       so this is the one behavioural difference between the demo and the shipped
       module, and it is copied verbatim from `signal-constellation.js` rather
       than reinvented, so both renderers see byte-identical input. */
    var unsubscribe = function () {};
    if (root.SignalEvents) {
      root.SignalEvents.replay(push);
      unsubscribe = root.SignalEvents.subscribe(push);
    }

    return {
      push: push,
      summary: summary,
      nodes: function () { return nodeList.slice(); },
      edges: function () { return edgeList.slice(); },
      edgeKey: edgeKey,
      isRunning: function () { return running; },

      /* The confound check, as a number rather than an assertion: how much trig
         this renderer does per frame versus what the shipped one does. */
      opsPerFrame: function () {
        return {
          nodes: nodeList.length,
          edges: edgeList.length,
          /* this file: 2 cos + 2 sin hoisted per FRAME, then per NODE
             cos(ang) + sin(ang) + 6 mul-adds for yaw/pitch + 1 divide */
          trigPerNodePerFrame: 2,
          trigHoistedPerFrame: 4,
          /* the shipped renderer calls project() per node AND per edge endpoint,
             each doing cos+sin => 2 per call */
          shippedTrigCalls: nodeList.length * 2 + edgeList.length * 4,
          thisTrigCalls: nodeList.length * 2 + 4,
          note: "Both renderers recompute their transform every frame, so neither " +
                "benefits from a cached-coordinate advantage. This file projects " +
                "ONCE per node per frame and reuses it for edges; the shipped one " +
                "re-projects both endpoints of every edge, so at these densities it " +
                "actually performs MORE trig calls. Disclosed because the prior " +
                "session's delta was ~half a caching artefact in the other direction."
        };
      },

      /* Depth diagnostics, so "there is visible depth variation" is a measurement
         rather than an impression. */
      depthStats: function () {
        if (!nodeList.length) return null;
        beginFrameTransform();
        var s = [], z = [];
        for (var i = 0; i < nodeList.length; i++) {
          project(nodeList[i]);
          s.push(nodeList[i]._s); z.push(nodeList[i]._z);
        }
        var ss = s.slice().sort(function (a, b) { return a - b; });
        var zz = z.slice().sort(function (a, b) { return a - b; });
        var q = function (arr, p) { return arr[Math.min(arr.length - 1, Math.floor(arr.length * p))]; };
        return {
          nodes: nodeList.length,
          scaleMin: +ss[0].toFixed(4), scaleP50: +q(ss, 0.5).toFixed(4),
          scaleMax: +ss[ss.length - 1].toFixed(4),
          nearFarRatio: +(ss[ss.length - 1] / ss[0]).toFixed(3),
          zMin: +zz[0].toFixed(1), zMax: +zz[zz.length - 1].toFixed(1),
          distinctScaleBuckets: (function () {
            var b = {}; for (var i = 0; i < s.length; i++) b[Math.round(s[i] * 50)] = 1;
            return Object.keys(b).length;
          })(),
          rotY: +rotY.toFixed(4), rotX: +rotX.toFixed(4)
        };
      },

      /* Proof the depth assignment is deterministic: same id -> same z, always. */
      zFor: function (id) { return ((hashZ(id) % 100000) / 100000 - 0.5) * Z_SPREAD; },

      reducedMotion: function () { return reduceMotion; },
      setSpin: function (v) { spin = v; },
      setParallax: function (on) { parallax.on = !!on; if (!on) { rotX = 0; } },
      step: function (dtMs) { rotY += spin * (dtMs || 16.67); draw(Date.now()); },
      drawOnly: function () { draw(Date.now()); },

      perf: function () {
        return { frames: stats.frames,
                 avgMs: stats.frames ? +(stats.totalMs / stats.frames).toFixed(3) : 0,
                 worstMs: +stats.worstMs.toFixed(3),
                 p95Ms: +percentile(stats.samples, 0.95).toFixed(3),
                 budgetMs: 16.67,
                 nodes: nodeList.length, edges: edgeList.length };
      },
      resetPerf: function () { stats = { frames: 0, totalMs: 0, worstMs: 0, samples: [] }; },
      destroy: function () {
        running = false;
        if (rafId) root.cancelAnimationFrame(rafId);
        if (io) io.disconnect();
        if (ro) ro.disconnect(); else root.removeEventListener("resize", resize);
        canvas.removeEventListener("mousemove", onMove);
        canvas.removeEventListener("mouseleave", onLeave);
        if (typeof document !== "undefined") {
          document.removeEventListener("visibilitychange", onVis);
          document.removeEventListener("scope:themechange", onTheme);
        }
        /* Without this a 2D<->3D toggle leaves the old renderer subscribed and
           still consuming every event — invisible, but it would keep growing. */
        unsubscribe();
      }
    };
  }

  root.ConstellationDepth = { mount: mount, TYPE_STYLE: TYPE_STYLE,
                              FOCAL: FOCAL, Z_SPREAD: Z_SPREAD };
})(window);
