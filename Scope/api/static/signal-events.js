/* ============================================================================
   SIGNAL EVENT BUS — the single path from a real Scope alert to any visual.

   Every visual subsystem (DNA helix, market pulse, and whatever comes next)
   subscribes HERE. Nothing may fetch `/alerts` for itself: two consumers with
   their own fetch loops drift the moment one of them filters differently, and
   then two components on the same page disagree about what happened. One
   stream, one adapter, one shape.

   ── THE HONESTY RULE THIS FILE ENFORCES ───────────────────────────────────
   Every field below comes from a real alert delivered by the real API. There
   is NO random generator, NO synthetic event, and NO fallback that invents
   activity when the network fails. A failed poll produces an ERROR state and
   zero events — a quiet helix is the truthful rendering of "we don't know",
   and a moving one would be a lie.

   ── HOW ALERTS ACTUALLY REACH THE BROWSER (measured, not assumed) ─────────
   There is NO push channel. Scope has no WebSocket and no SSE endpoint; the
   only `StreamingResponse` in the API is a CSV download (`routers/history.py:187`).
   Alerts arrive by `fetch()` polling, and the existing feed page polls only a
   count badge, every five minutes (`alerts.html:1153`).

   What makes honest near-real-time possible is the cursor already built into
   the list endpoint:

       routers/alerts.py:93-95
           if since:
               conditions.append("datetime(a.created_at) > datetime(:since)")

   So `GET /alerts?since=<ts>` returns only what is genuinely new. This module
   polls that cursor. ⚠️ THAT IS POLLING, NOT PUSH — "live" here means "checked
   every POLL_MS", and `SignalEvents.describeTransport()` says so out loud so a
   UI can print it rather than implying a socket.
   ============================================================================ */
(function (root) {
  "use strict";

  /* Poll cadence. Prod cadence measured over 500 alerts spanning 2026-08-10 →
     08-12: median 5 alerts/hour, max 64. At 30s most polls return an empty
     array, which is correct and cheap (the query is indexed on created_at and
     bounded by `since`). Faster buys nothing real; slower makes the pulse lag
     a burst it should show. */
  var POLL_MS = 30000;

  /* The default view deliberately inherits the SERVER's editorial default.
     `/alerts` with no `rule` param excludes RULE_07 / RULE_OSINT / RULE_REDDIT
     (`routers/alerts.py:82-85`) — a decision already made and reviewed in the
     product, not one invented here. Pass {allRules:true} for the raw firehose;
     measured on prod, those three are 34.8% of it. (An earlier comment here said
     68% — that was wrong: it counted RULE_ANOMALY, which the default view KEEPS.) */
  var LOOKBACK_DAYS = 2;
  var SEED_LIMIT = 120;

  /* ⚠️ A FULL PAGE MEANS THERE IS MORE, AND THE CURSOR MUST NOT JUMP OVER IT.
     This was a real silent-loss bug, found by an independent verifier by
     execution. `/alerts` orders `created_at DESC` and truncates
     (`routers/alerts.py:180,206`), so one request returns the NEWEST n after the
     cursor — not the oldest. Advancing the cursor to the newest row therefore
     skipped everything between the old cursor and the oldest row returned, and
     `since` excluded it FOREVER. Measured in the verifier's harness: a gap
     holding 251 alerts delivered 121 and permanently lost 130 — while the status
     line still read "live". That is worse than a failed poll, because the page
     promises a failed poll shows as quiet.

     Reachable by any gap beyond ~2h at the measured peak (63 alerts/hr): laptop
     sleep, background-tab throttling, a deploy, an API restart.

     The fix uses the endpoint's PAGINATED form, which the list route already
     supports (`routers/alerts.py:183-199`) and which returns `total` and `pages`
     — so the backlog is a number we can read rather than guess. Pages are
     drained oldest-last, and THE CURSOR ONLY MOVES ONCE THE DRAIN COMPLETES, so
     a row missed because new alerts shifted pagination mid-drain is still
     `> cursor` and is picked up by the next poll. Self-healing rather than
     silently lossy. */
  var PAGE_SIZE = 100;              /* the endpoint's own maximum (`le=100`) */
  var MAX_DRAIN_PAGES = 25;         /* 2,500 rows; beyond that we say so out loud */

  var subscribers = [];
  var recent = [];              /* newest-first, capped — what subscribers replay */
  var RECENT_CAP = 400;

  /* ⚠️ THE DEDUP SET MUST OUTLIVE THE DISPLAY BUFFER. It was previously pruned
     alongside `recent`, so an id evicted from the buffer could be emitted a
     second time. That was latent while the cursor only ever moved forward; the
     boundary-second overlap below makes the bus re-see rows ON PURPOSE, so the
     dedup now has its own, much larger, FIFO bound. */
  var seenIds = Object.create(null);
  var seenOrder = [];
  var SEEN_CAP = 5000;

  var ruleModel = null;         /* from /api/rule-model — the GATE's own map */
  var cursor = null;            /* ISO timestamp of the newest alert seen */
  var timer = null;
  var started = false;
  var status = { state: "idle", lastPoll: null, lastError: null, transport: "poll",
                 backlog: 0, truncated: false };

  /* ── the normalized event ────────────────────────────────────────────────
     Built ONLY from fields the API genuinely returns. Verified against a live
     payload: the `/alerts` SELECT (`routers/alerts.py:170-178`) yields exactly
     id, rule, ticker, severity, headline, detail, tags, member_id, created_at,
     source_url, time_horizon, novelty_score, opportunity_score,
     evidence_confidence, source_quality, verify_url, theme_id, lifecycle_stage,
     full_name, party, state — plus a server-assembled `receipts` block.

     ⚠️ FIELDS THE BRIEF ASKED FOR THAT DO NOT EXIST SERVER-SIDE TODAY. They are
     left null and are NOT reconstructed client-side; see the session note, each
     is a named API requirement rather than a guess:
       • `location`   — no such column, and no alert carries one. Permanently
                        null here. A consumer must treat null as "unknown".
       • `corroborates` — the per-alert signed-leg verdict EXISTS in the
                        database and is NOT in the `/alerts` SELECT, so the
                        browser cannot see it.
       • theme membership on contributing legs — only the RULE_10 synthesis
                        alert carries `theme_id`; the RULE_06/11/15 legs that
                        actually corroborated do NOT. Measured on prod: of 50
                        RTX alerts, exactly 1 has a theme_id. So a leg that DID
                        corroborate is indistinguishable from one that did not,
                        and this module refuses to guess.
     ──────────────────────────────────────────────────────────────────────── */

  function parseTags(raw) {
    /* `tags` is a TEXT column with two shapes in the wild: JSON (RULE_10 and
       friends) and free CSV ("4 signals,4.0x"). Only the JSON form carries
       structured data; anything else yields {} rather than a bad parse. */
    if (!raw || typeof raw !== "string") return {};
    var t = raw.trim();
    if (t.charAt(0) !== "{") return {};
    try { var o = JSON.parse(t); return (o && typeof o === "object") ? o : {}; }
    catch (_) { return {}; }
  }

  function toMillis(createdAt) {
    /* `created_at` is naive UTC ("2026-08-12 12:09:15"). Date.parse on that
       string is implementation-defined — Safari has historically read it as
       local time, which would shift every rung by the viewer's offset. Parse
       the parts explicitly and build it as UTC. */
    if (!createdAt) return NaN;
    var m = String(createdAt).trim()
      .match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})/);
    if (!m) { var d = Date.parse(createdAt); return isNaN(d) ? NaN : d; }
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  }

  /* ── category ────────────────────────────────────────────────────────────
     The gate counts INSTRUMENTS, not rule names — four congressional rules are
     one instrument. `/api/rule-model` (`main.py:789`) derives `instrument_of`
     from `jpt_common` by import, precisely so display code never copies the
     list and drifts. We read it from there and NEVER hardcode a mapping.

     A rule with no instrument is genuinely excluded from corroboration
     (RULE_07, RULE_ANOMALY, RULE_08, RULE_09 …). That is not a gap in our
     data — it is a real property of the rule, so it gets its own honest
     category, "context", and the DNA colours it as such. */
  function categoryFor(rule) {
    if (!rule) return { category: "unknown", eligible: false };
    /* ⚠️ RULE_10 gets its own category but is NOT gate-eligible: it is IN
       `RULE_10_EXCLUDED` (`jpt_common.py`) because it is the gate itself, not a
       peer instrument. Saying `eligible: true` here was factually wrong — caught
       by the verifier — and would have let a future consumer draw the gate as one
       of its own legs. `/api/rule-model` reports it separately for this reason. */
    if (rule === "RULE_10") return { category: "corroboration", eligible: false };
    var map = (ruleModel && ruleModel.instrument_of) || null;
    if (!map) return { category: "unknown", eligible: false };
    var inst = map[rule];
    if (inst) return { category: inst, eligible: true };
    return { category: "context", eligible: false };
  }

  function entitiesFor(a) {
    /* Real entities only: the member (if the alert has one) and the ticker.
       No NER, no inference from headline text. */
    var out = [];
    if (a.full_name) {
      out.push({
        type: "member", name: a.full_name, id: a.member_id || null,
        party: a.party || null, state: a.state || null
      });
    }
    if (a.ticker) out.push({ type: "ticker", name: a.ticker, id: a.ticker });
    return out;
  }

  /* ── the adapter — the ONLY place a raw alert becomes an event ─────────── */
  function adapt(a) {
    if (!a || a.id == null) return null;
    var cat = categoryFor(a.rule);
    var tags = parseTags(a.tags);

    /* is_corroborated is a FACT ABOUT THIS ROW, not an inference. `theme_id`
       is set by RULE_10 when it groups signals into a theme, so a row carrying
       one is a row the gate actually corroborated. Everything else is false —
       including legs that may well have contributed, because the payload does
       not say so (see the note above). */
    var isCorr = a.theme_id !== null && a.theme_id !== undefined;

    /* instrument_count comes from the alert's own tags where RULE_10 wrote it
       (verified live: {"instruments":["contracts","earnings","insider"],
       "instrument_count":3}). Never derived, never defaulted to 1. */
    var n = tags.instrument_count;
    n = (typeof n === "number" && isFinite(n)) ? n : null;

    /* `evidence_confidence` is a real stored score. It is NOT a probability and
       is not rescaled here — passed through so a consumer can decide. */
    var conf = (typeof a.evidence_confidence === "number") ? a.evidence_confidence : null;

    return {
      event_id: "alert:" + a.id,
      alert_id: a.id,
      timestamp: toMillis(a.created_at),
      iso: a.created_at || null,
      category: cat.category,
      gate_eligible: cat.eligible,
      rule: a.rule || null,
      severity: (a.severity || "").toUpperCase() || null,
      corroboration: {
        is_corroborated: isCorr,
        instrument_count: n,
        confidence: conf,
        theme_id: (a.theme_id != null ? a.theme_id : null)
      },
      entities: entitiesFor(a),
      location: null,            /* NOT AVAILABLE server-side — see header */
      ticker: a.ticker || null,
      headline: a.headline || null,
      lifecycle_stage: a.lifecycle_stage || null
    };
  }

  /* ── bus ─────────────────────────────────────────────────────────────── */
  function subscribe(fn) {
    if (typeof fn !== "function") return function () {};
    subscribers.push(fn);
    return function unsubscribe() {
      var i = subscribers.indexOf(fn);
      if (i >= 0) subscribers.splice(i, 1);
    };
  }

  function emit(evt) {
    if (!evt) return;
    if (seenIds[evt.event_id]) return;        /* a poll overlap must not double-count */
    seenIds[evt.event_id] = 1;
    seenOrder.push(evt.event_id);
    if (seenOrder.length > SEEN_CAP) {
      var gone = seenOrder.splice(0, seenOrder.length - SEEN_CAP);
      for (var g = 0; g < gone.length; g++) delete seenIds[gone[g]];
    }
    recent.unshift(evt);
    if (recent.length > RECENT_CAP) recent.splice(RECENT_CAP);
    for (var i = 0; i < subscribers.length; i++) {
      /* One throwing subscriber must not stop the others, and must not kill the
         poll loop — a broken visual should degrade alone. */
      try { subscribers[i](evt); } catch (e) { if (root.console) console.error("[signal-events] subscriber threw", e); }
    }
  }

  function setStatus(state, err) {
    status.state = state;
    status.lastError = err || null;
    status.lastPoll = Date.now();
    for (var i = 0; i < subscribers.length; i++) {
      try { if (subscribers[i].onStatus) subscribers[i].onStatus(getStatus()); } catch (_) {}
    }
    if (typeof root.dispatchEvent === "function" && typeof root.CustomEvent === "function") {
      root.dispatchEvent(new CustomEvent("signal-events:status", { detail: getStatus() }));
    }
  }

  function getStatus() {
    return {
      state: status.state, lastPoll: status.lastPoll, lastError: status.lastError,
      transport: "poll", pollMs: POLL_MS, count: recent.length,
      cursor: cursor, ruleModelLoaded: !!ruleModel,
      /* rows the server said matched the last drain, and whether we stopped
         short of them. `truncated: true` means the helix IS incomplete and the
         UI must not read as "live". */
      backlog: status.backlog, truncated: status.truncated,
      lookbackDays: LOOKBACK_DAYS
    };
  }

  function describeTransport() {
    /* For UI copy. Deliberately does not say "live" or "real-time" unqualified. */
    return "polling /alerts every " + Math.round(POLL_MS / 1000) + "s (no push channel exists)";
  }

  /* ── the feed ────────────────────────────────────────────────────────── */
  var opts = { allRules: false };

  function alertsUrl(sinceIso, page) {
    var p = ["days=" + LOOKBACK_DAYS];
    if (page) p.push("page=" + page, "per_page=" + PAGE_SIZE);
    else p.push("limit=" + SEED_LIMIT);
    if (opts.allRules) p.push("rule=ALL");
    if (sinceIso) p.push("since=" + encodeURIComponent(sinceIso));
    return "/alerts?" + p.join("&");
  }

  /* ⚠️ THE CURSOR DELIBERATELY LAGS THE NEWEST ROW BY ONE SECOND.
     `alerts.created_at` is second-resolution and the endpoint filters
     `datetime(a.created_at) > datetime(:since)` — STRICTLY greater
     (`routers/alerts.py:93-95`). Measured on prod over 500 alerts: **50% share
     their second with another alert**, worst case 28 rows inside
     `2026-08-11 08:32:53`. So a row written into the cursor's own second after
     the poll returned would be skipped FOREVER, silently, and the helix would
     just be quietly incomplete. Backing off a second re-queries that boundary
     on every poll; `seenIds` throws away the handful of duplicates. Cheap
     overlap beats silent loss. */
  function backOffOneSecond(iso) {
    var m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})/);
    if (!m) return iso;
    var d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) - 1000);
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return d.getUTCFullYear() + "-" + p(d.getUTCMonth() + 1) + "-" + p(d.getUTCDate())
         + " " + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()) + ":" + p(d.getUTCSeconds());
  }

  function ingest(rows) {
    if (!Array.isArray(rows)) return 0;
    /* The API returns newest-first. Emit OLDEST-first so a subscriber that
       tracks recency (the DNA's age fade, the pulse's window) sees time move
       forward, not backward. */
    var evts = [];
    for (var i = 0; i < rows.length; i++) {
      var e = adapt(rows[i]);
      if (e && !isNaN(e.timestamp)) evts.push(e);
    }
    evts.sort(function (a, b) { return a.timestamp - b.timestamp; });
    var newest = null;
    for (var j = 0; j < evts.length; j++) {
      if (evts[j].iso && (!newest || evts[j].iso > newest)) newest = evts[j].iso;
      emit(evts[j]);
    }
    return { count: evts.length, newest: newest };
  }

  function getJson(url) {
    return fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      });
  }

  /* The FIRST fetch is a bounded seed, not a backfill: the newest SEED_LIMIT
     rows, so the helix opens with real context instead of empty. Rows older than
     that are not fetched and are not "lost" — they were never in scope. Every
     poll AFTER the seed drains completely. */
  function seed() {
    return getJson(alertsUrl(null)).then(function (rows) {
      var r = ingest(rows);
      if (r.newest) cursor = backOffOneSecond(r.newest);
      status.backlog = 0;
      status.truncated = false;
      setStatus("live");
    });
  }

  function drain() {
    var startCursor = cursor;
    var newest = null;
    var truncated = false;
    var total = 0;

    function page(n) {
      return getJson(alertsUrl(startCursor, n)).then(function (body) {
        var items = (body && body.items) || [];
        total = (body && typeof body.total === "number") ? body.total : items.length;
        var r = ingest(items);
        if (r.newest && (!newest || r.newest > newest)) newest = r.newest;
        var pages = (body && body.pages) || 1;
        if (n >= pages) return;
        if (n >= MAX_DRAIN_PAGES) { truncated = true; return; }
        return page(n + 1);
      });
    }

    return page(1).then(function () {
      /* ⚠️ THE CURSOR MOVES ONLY HERE, after every page landed. */
      if (newest) cursor = backOffOneSecond(newest);
      status.backlog = total;
      status.truncated = truncated;
      setStatus(truncated ? "truncated" : "live");
    });
  }

  function poll() {
    if (!started) return;
    var work = (cursor === null) ? seed() : drain();
    work.catch(function (err) {
      /* ⚠️ NO SYNTHETIC FALLBACK. The state goes to "error", no event is
         emitted, and every subscriber simply has nothing new to draw. The
         cursor is NOT advanced, so the next poll re-requests the same window. */
      setStatus("error", String(err && err.message || err));
    });
  }

  function start(options) {
    if (started) return;
    if (options && typeof options === "object") {
      if (options.allRules === true) opts.allRules = true;
      if (typeof options.pollMs === "number" && options.pollMs >= 5000) POLL_MS = options.pollMs;
    }
    started = true;
    setStatus("connecting");

    /* The rule model must land BEFORE the first alerts, or the seed batch gets
       categorised "unknown" and the helix opens on a lie of omission. */
    fetch("/api/rule-model", { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (m) { if (m && m.instrument_of) ruleModel = m; })
      .catch(function () { ruleModel = null; })
      .then(function () {
        poll();
        timer = setInterval(poll, POLL_MS);
      });
  }

  function stop() {
    started = false;
    if (timer) { clearInterval(timer); timer = null; }
    setStatus("stopped");
  }

  /* Replay for a subscriber that mounts after the seed has already landed —
     real stored events, not regenerated ones. */
  function replay(fn, limit) {
    if (typeof fn !== "function") return;
    var n = (typeof limit === "number") ? limit : recent.length;
    var slice = recent.slice(0, n).slice().reverse();   /* oldest-first */
    for (var i = 0; i < slice.length; i++) { try { fn(slice[i]); } catch (_) {} }
  }

  root.SignalEvents = {
    subscribe: subscribe,
    emit: emit,
    adapt: adapt,
    start: start,
    stop: stop,
    replay: replay,
    getRecent: function () { return recent.slice(); },
    getStatus: getStatus,
    describeTransport: describeTransport,
    getRuleModel: function () { return ruleModel; },
    /* Test seam: hand the adapter a REAL alert payload (e.g. the stored RULE_10
       row fetched from the API) and push it through the one true path. This is
       not a generator — it cannot produce an event without a real alert
       object being handed to it. */
    /* Returns the COUNT, as it always has. `ingest()` began returning
       {count, newest} when the drain took ownership of the cursor; leaking that
       object through this public seam silently changed its contract. */
    ingestAlerts: function (rows) { return ingest(rows).count; },
    POLL_MS: function () { return POLL_MS; }
  };
})(window);
