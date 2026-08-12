/* ============================================================================
   SIGNAL CONSTELLATION — a relationship graph of what Scope's alerts ACTUALLY
   say is related.

   A subscriber to `signal-events.js`, like the DNA and the pulse. It has no
   fetch of its own. It draws nodes and edges ONLY from `event.entities` and
   `event.relations`, both of which the adapter builds from named stored fields
   and neither of which contains an inferred link.

   ── THE ONE RULE ──────────────────────────────────────────────────────────
   AN EDGE IS A CLAIM. Drawing a line between two nodes asserts that Scope's
   data says they are related. Co-occurrence is not a relationship: two alerts
   arriving in the same minute, two companies in the same sector, a ticker that
   a rule attached from a keyword basket — none of those earn a line. This file
   contains no edge-creation path other than iterating `event.relations`, and
   the honesty audit greps for exactly that.

   ── THE GRAPH THE REAL DATA SUPPORTS ──────────────────────────────────────
   Every edge below is `something → ticker`, because the stored row is what ties
   each party to a symbol, and no stored field relates two non-ticker parties to
   each other. Multi-hop happens through the shared ticker node — which is a
   real path ("this member traded the same name this contractor was awarded"),
   not a synthesised one.

     member      → ticker    alerts.member_id + full_name   (RULE_01B)
                             tags.members[] + member_names[] (RULE_CLUSTER)
     insider     → ticker    tags[0..-2]                     (RULE_06)
     institution → ticker    tags.filer + tags.cik           (RULE_16)
     contractor  → ticker    tags|0                          (RULE_11)
     ticker      → instrument tags.instruments[]             (RULE_10 only —
                             the gate's own assertion, not our classification)

   ── AND WHAT IS DELIBERATELY ABSENT ───────────────────────────────────────
   The design brief's chain is "person → organization → government → contract →
   company → ticker". Three links are missing here, and an independent verifier
   corrected TWO of my reasons — the behaviour was right, the justification was
   not. See `signal-events.js` for the measurements:
     • RULE_08 — real agency, basket-fabricated ticker. Refused outright. (Reason
       upheld: the only edge it could make is one the gate rules false.)
     • contract AGENCY — ⚠️ NOT "unbuildable". `/contracts/data` is live on prod
       and returns `contracts.agency`; 182 of 183 RULE_11 award ids join to it.
       It is not on the `/alerts` payload, so consuming it needs a SECOND stream,
       which the bus's one-stream rule forbids. An architectural gap, not an
       evidential one.
     • RULE_02 members — ⚠️ NOT "unrecoverable". 96 of 96 prod rows satisfy
       `comma-fields == 2 × the member count in the headline`, so a count-
       validated pairwise split recovers them and fails closed. The real
       objection is IDENTITY: recovered names are not bioguide ids, so they would
       mint a SECOND node for a member already present as `M:<bioguide>` — two
       nodes for one person is a worse lie than one missing edge. The ids exist
       in `rule_02_cluster.py`'s `why_matters`; they are just not on the payload.
   ============================================================================ */
(function (root) {
  "use strict";

  /* Node cap, chosen from measured volume rather than a round number. The bus
     seeds and polls a 2-day window; measured on prod, that window holds 306
     alerts carrying 198 distinct tickers, ~80 distinct insider owners and 5
     distinct members. 320 covers a full window of real entities with headroom,
     and eviction is least-recently-touched so a long session sheds the stale
     rather than the important. Edges are bounded by node eviction plus a hard
     ceiling for the pathological case of one hub with hundreds of spokes. */
  var MAX_NODES = 320;
  var MAX_EDGES = 700;

  var NEW_EDGE_ANIM_MS = 1100;      /* the "animate in" the brief asks for */
  var REINFORCE_MS     = 900;       /* a repeat pulses the EXISTING edge */

  var TYPE_STYLE = {
    /* dark, light */
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

    var nodes = Object.create(null);   /* id -> node */
    var nodeList = [];
    var edges = Object.create(null);   /* key -> edge */
    var edgeList = [];

    var running = false, rafId = null, lastTs = 0;
    var onScreen = true;
    var visible = (typeof document === "undefined") || document.visibilityState !== "hidden";
    var stats = { frames: 0, totalMs: 0, worstMs: 0, pauses: 0 };
    /* `edgesEvicted` exists because the verifier could not reconcile the
       counters without it: 952 edgesAdded against 170 live edges, with 782
       having vanished uncounted. The node counters balanced; the edge ones
       silently did not. A counter you cannot reconcile is not evidence. */
    var counters = { nodesAdded: 0, edgesAdded: 0, edgesReinforced: 0,
                     eventsSeen: 0, eventsWithNoRelation: 0,
                     nodesEvicted: 0, edgesEvicted: 0 };

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

    /* ── deterministic placement ─────────────────────────────────────────────
       ⚠️ NO Math.random ANYWHERE, and not only for the honesty audit's sake: a
       random layout would move every node on reload, so the same data would
       look like different data. Position is hashed from the node id, so a given
       entity lands in the same place every time and two sessions are
       comparable. Type decides the ring; the hash decides the angle. */
    function hash(str) {
      var h = 2166136261;
      for (var i = 0; i < str.length; i++) {
        h ^= str.charCodeAt(i);
        h = (h + (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24)) >>> 0;
      }
      return h >>> 0;
    }
    var RING = { instrument: 0.10, ticker: 0.40, member: 0.78,
                 insider: 0.72, institution: 0.86, contractor: 0.92 };

    function place(node) {
      var h = hash(node.id);
      var ang = (h % 100000) / 100000 * Math.PI * 2;
      var jitter = ((h >>> 17) % 1000) / 1000 * 0.07 - 0.035;
      node.ring = (RING[node.type] != null ? RING[node.type] : 0.6) + jitter;
      node.ang = ang;
    }

    function project(node) {
      var cx = W / 2, cy = H / 2;
      var rad = Math.min(W, H) * 0.46 * node.ring;
      return { x: cx + Math.cos(node.ang) * rad * 1.55, y: cy + Math.sin(node.ang) * rad };
    }

    /* ── the only way a node or edge ever comes into existence ─────────────── */
    /* `hits` counts RELATIONSHIP TOUCHES, not alerts — a 3-member cluster row
       takes its ticker node to hits=3 in one event. That is the intended
       reading (three relationships landed on it) and it drives node radius, but
       it is NOT an alert count and must not be reported as one. */
    function touchNode(ent, now) {
      var n = nodes[ent.id];
      if (n) {
        n.lastSeen = now;
        n.hits++;
        return n;
      }
      n = { id: ent.id, type: ent.type, name: ent.name,
            sourceField: ent.sourceField || null,
            born: now, lastSeen: now, hits: 1, appear: now };
      place(n);
      nodes[ent.id] = n;
      nodeList.push(n);
      counters.nodesAdded++;
      evictIfNeeded(now);
      return n;
    }

    /* THE DEDUP KEY. An edge is identified by (from, to, kind) — the two entity
       ids plus the relationship type, all three of which come from stored
       fields. The SAME member trading the SAME ticker again is the same
       relationship: it must reinforce, never duplicate. A DIFFERENT relation
       kind between the same pair (were one ever to exist) stays distinct,
       because "traded" and "was awarded" are not the same claim. */
    function edgeKey(rel) { return rel.from + "|" + rel.to + "|" + rel.kind; }

    function touchEdge(rel, now) {
      var key = edgeKey(rel);
      var e = edges[key];
      if (e) {
        e.hits++;
        e.lastSeen = now;
        e.reinforcedAt = now;          /* pulse the EXISTING line */
        counters.edgesReinforced++;
        return e;
      }
      e = { key: key, from: rel.from, to: rel.to, kind: rel.kind, verb: rel.verb,
            sourceField: rel.sourceField || null,
            born: now, lastSeen: now, hits: 1, appear: now, reinforcedAt: 0 };
      edges[key] = e;
      edgeList.push(e);
      counters.edgesAdded++;
      if (edgeList.length > MAX_EDGES) {
        edgeList.sort(function (a, b) { return a.lastSeen - b.lastSeen; });
        var drop = edgeList.splice(0, edgeList.length - MAX_EDGES);
        for (var i = 0; i < drop.length; i++) { delete edges[drop[i].key]; counters.edgesEvicted++; }
      }
      return e;
    }

    function evictIfNeeded(now) {
      if (nodeList.length <= MAX_NODES) return;
      nodeList.sort(function (a, b) { return a.lastSeen - b.lastSeen; });
      var drop = nodeList.splice(0, nodeList.length - MAX_NODES);
      for (var i = 0; i < drop.length; i++) {
        delete nodes[drop[i].id];
        counters.nodesEvicted++;
      }
      /* An edge whose endpoint is gone is no longer drawable — drop it rather
         than leave a line to nowhere. */
      var keep = [];
      for (var j = 0; j < edgeList.length; j++) {
        var e = edgeList[j];
        if (nodes[e.from] && nodes[e.to]) keep.push(e);
        else { delete edges[e.key]; counters.edgesEvicted++; }
      }
      edgeList = keep;   /* reassigns the closure `var` at the top of mount() —
                            confirmed behaviourally: 0 dangling edges after 949
                            real evictions and a synthetic hub-heavy fill. */
    }

    /* ── the subscriber ───────────────────────────────────────────────────── */
    function push(evt) {
      if (!evt) return;
      counters.eventsSeen++;
      var rels = evt.relations || [];
      if (!rels.length) { counters.eventsWithNoRelation++; return; }

      /* ⚠️ Nodes are created ONLY for entities that an edge actually uses. An
         alert that names a ticker and nothing else (RULE_15, RULE_ANOMALY)
         carries no relationship, so it contributes no node — a lone dot would
         imply a graph entry where there is none. */
      var byId = Object.create(null);
      for (var i = 0; i < (evt.entities || []).length; i++) byId[evt.entities[i].id] = evt.entities[i];

      var now = Date.now();
      for (var j = 0; j < rels.length; j++) {
        var r = rels[j];
        var a = byId[r.from], b = byId[r.to];
        if (!a || !b) continue;          /* never invent an endpoint */
        touchNode(a, now);
        touchNode(b, now);
        touchEdge(r, now);
      }
      /* ⚠️ NO SYNCHRONOUS DRAW HERE. This used to call `draw()` whenever the rAF
         loop was paused — but "paused" means off-screen or backgrounded, so
         nobody could see the result and the work was pure waste. The verifier
         measured 200 draws executed with BOTH pause conditions true. The loop
         redraws on resume (`sync()` -> `frame()` -> `draw()`), which is when a
         viewer can actually see it. */
      if (typeof opt.onChange === "function") opt.onChange(summary());
    }

    function summary() {
      var byType = {};
      for (var i = 0; i < nodeList.length; i++) byType[nodeList[i].type] = (byType[nodeList[i].type] || 0) + 1;
      var byKind = {};
      for (var j = 0; j < edgeList.length; j++) byKind[edgeList[j].kind] = (byKind[edgeList[j].kind] || 0) + 1;
      return { nodes: nodeList.length, edges: edgeList.length,
               nodesByType: byType, edgesByKind: byKind, counters: counters,
               caps: { nodes: MAX_NODES, edges: MAX_EDGES } };
    }

    /* ── drawing. No ctx.filter, no shadowBlur, no per-frame patterns. ────── */
    function draw(now) {
      var t0 = (root.performance && performance.now) ? performance.now() : 0;
      ctx.clearRect(0, 0, W, H);

      if (!nodeList.length) {
        ctx.strokeStyle = themeIsLight() ? "rgba(90,90,100,0.18)" : "rgba(120,120,132,0.14)";
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(W / 2, H / 2, Math.min(W, H) * 0.3, 0, Math.PI * 2); ctx.stroke();
        finishFrame(t0);
        return;
      }

      for (var i = 0; i < edgeList.length; i++) drawEdge(edgeList[i], now);
      for (var j = 0; j < nodeList.length; j++) drawNode(nodeList[j], now);
      finishFrame(t0);
    }

    function finishFrame(t0) {
      var t1 = (root.performance && performance.now) ? performance.now() : 0;
      stats.frames++;
      var ms = t1 - t0;
      stats.totalMs += ms;
      if (ms > stats.worstMs) stats.worstMs = ms;
    }

    function drawEdge(e, now) {
      var a = nodes[e.from], b = nodes[e.to];
      if (!a || !b) return;
      var pa = project(a), pb = project(b);

      /* ANIMATE IN: a genuinely new relationship draws itself from one end to
         the other over NEW_EDGE_ANIM_MS. Nothing else in this file animates on
         a timer, so motion here means "a new claim just arrived". */
      var age = now - e.appear;
      var t = clamp(age / NEW_EDGE_ANIM_MS, 0, 1);
      var ease = 1 - Math.pow(1 - t, 3);
      var ex = pa.x + (pb.x - pa.x) * ease;
      var ey = pa.y + (pb.y - pa.y) * ease;

      /* REINFORCE: a repeat of a known relationship brightens the existing line
         instead of adding a second one. */
      var rein = e.reinforcedAt ? clamp(1 - (now - e.reinforcedAt) / REINFORCE_MS, 0, 1) : 0;

      var col = colorOf(nodes[e.from].type === "ticker" ? nodes[e.to].type : nodes[e.from].type);
      var weight = clamp(0.6 + Math.log(1 + e.hits) * 0.55, 0.6, 2.6);
      var alpha = clamp(0.16 + Math.log(1 + e.hits) * 0.11 + rein * 0.55, 0.10, 0.95);

      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(ex, ey);
      ctx.strokeStyle = "rgba(" + col[0] + "," + col[1] + "," + col[2] + "," + alpha.toFixed(3) + ")";
      ctx.lineWidth = weight + rein * 1.2;
      ctx.stroke();

      if (t < 1) {
        /* the travelling head, so an arriving edge reads as arriving */
        ctx.beginPath();
        ctx.arc(ex, ey, 2.0, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(" + col[0] + "," + col[1] + "," + col[2] + ",0.9)";
        ctx.fill();
      }
    }

    function drawNode(n, now) {
      var p = project(n);
      var col = colorOf(n.type);
      var pop = clamp((now - n.appear) / 420, 0, 1);
      var r = radiusOf(n.type) * (0.35 + 0.65 * pop) * (1 + Math.min(0.5, Math.log(1 + n.hits) * 0.16));
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(" + col[0] + "," + col[1] + "," + col[2] + ",0.92)";
      ctx.fill();
    }

    function frame(ts) {
      if (!running) return;
      if (!lastTs) lastTs = ts;
      lastTs = ts;
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
    function onTheme() { if (!running) draw(Date.now()); }
    if (typeof document !== "undefined") document.addEventListener("scope:themechange", onTheme);

    var ro = null;
    if (typeof ResizeObserver === "function") {
      ro = new ResizeObserver(function () { resize(); if (!running) draw(Date.now()); });
      ro.observe(canvas);
    } else {
      root.addEventListener("resize", resize);      /* removed in destroy() */
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
      summary: summary,
      nodes: function () { return nodeList.slice(); },
      edges: function () { return edgeList.slice(); },
      edgeKey: edgeKey,
      isRunning: function () { return running; },
      isOnScreen: function () { return onScreen; },
      isTabVisible: function () { return visible; },
      perf: function () {
        return { frames: stats.frames,
                 avgMs: stats.frames ? +(stats.totalMs / stats.frames).toFixed(3) : 0,
                 worstMs: +stats.worstMs.toFixed(3),
                 pauses: stats.pauses, budgetMs: 16.67,
                 nodes: nodeList.length, edges: edgeList.length };
      },
      resetPerf: function () { stats = { frames: 0, totalMs: 0, worstMs: 0, pauses: stats.pauses }; },
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

  root.SignalConstellation = { mount: mount, TYPE_STYLE: TYPE_STYLE,
                               MAX_NODES: MAX_NODES, MAX_EDGES: MAX_EDGES };
})(window);
