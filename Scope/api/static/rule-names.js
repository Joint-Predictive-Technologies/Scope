/* ============================================================================
   Rule DISPLAY names — presentation only.

   The programmatic ids (RULE_06, RULE_COLLECTOR, …) are the contract between
   the rules, the gate, `activity_log` and the API. They are NOT renamed here
   and must never be: this file only decides what a human reads on screen.
   Look a rule up with `ruleName(id)`; the id itself is untouched.

   Names are taken from what each rule actually does in code, not from what its
   label suggests. Two worth calling out:
     * RULE_06 is "Insider trades", not "Insider buying" — `majority_action`
       (rule_06_form4.py:105-110) returns "bought" OR "sold" and the rule emits
       both, so a buy-only name would misdescribe roughly half its output.
     * RULE_12/13/14 are marked retired because they are: unscheduled and in
       RULE_10_EXCLUDED since 2026-07-27. Their alerts are still in the archive,
       so the label has to stay honest about their status.

   Unmapped ids fall back to a readable form of the id itself (RULE_FOO → "Foo"),
   never a crash and never a bare "RULE_FOO".
   ============================================================================ */
(function (root) {
  "use strict";

  var RULE_NAMES = {
    // ── congressional disclosures (one instrument, several views) ──
    RULE_01:             "Congressional trades",
    RULE_01B:            "First position",
    RULE_02:             "Congressional cluster (7d)",
    RULE_CLUSTER:        "Congressional cluster (72h)",

    // ── corporate filings ──
    RULE_06:             "Insider trades",
    RULE_15:             "Earnings call language",
    RULE_16:             "Institutional 13F",

    // ── government activity ──
    RULE_08:             "Federal Register",
    RULE_09:             "Lobbying spike",
    RULE_11:             "Government contracts",

    // ── synthesis ──
    RULE_10:             "Corroboration",

    // ── open-source / market chatter (excluded from corroboration as noise) ──
    RULE_07:             "Prediction markets",
    RULE_OSINT:          "Geopolitical events",
    RULE_TELEGRAM_OSINT: "Telegram OSINT",
    RULE_ADSB:           "Aircraft movements",
    RULE_REDDIT:         "Reddit chatter",
    RULE_COLLECTOR:      "Reddit coverage",
    RULE_ANOMALY:        "Attention spike",
    RULE_OPTIONS:        "Options activity",

    // ── retired: kept so archived alerts still read honestly ──
    RULE_12:             "Foreign lobbying (retired)",
    RULE_13:             "PAC finance (retired)",
    RULE_14:             "Patent clusters (retired)",

    // ── non-rule activity_log sources that surface on /status ──
    SCORING:                 "Score enrichment",
    DECAY:                   "Alert decay",
    BACKTEST:                "Backtest",
    BRIEF:                   "Daily brief",
    DAILY_BRIEF:             "Daily brief",
    LABEL_OUTCOMES:          "Outcome labelling",
    INGEST_HOUSE_INDEX:      "House filings ingest",
    PARSE_HOUSE_PDFS:        "House PDF parse",
    INGEST_SENATE:           "Senate ingest",
    ROSTER_CHECK:            "Roster check",
    DB_BACKUP:               "Database backup",
    RULE_DISCOVERY:          "Discovery",
    RULE_11_MIGRATION:       "Contracts migration",
    SCHEDULER_JOB_FAILURE:   "Scheduler failure"
  };

  /* Readable label for a rule id. Unknown ids degrade to a title-cased form of
     the id rather than showing the raw token or throwing. */
  function ruleName(id) {
    if (id == null) return "";
    var key = String(id).trim().toUpperCase();
    if (!key) return "";
    if (RULE_NAMES[key]) return RULE_NAMES[key];
    return key
      .replace(/^RULE[_-]/, "")
      .replace(/[_-]+/g, " ")
      .toLowerCase()
      .replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  /* True when we have a curated name (useful for deciding whether to also show
     the raw id as a tooltip). */
  function hasRuleName(id) {
    return Object.prototype.hasOwnProperty.call(
      RULE_NAMES, String(id == null ? "" : id).trim().toUpperCase());
  }

  root.RULE_NAMES = RULE_NAMES;
  root.ruleName = ruleName;
  root.hasRuleName = hasRuleName;
})(window);
