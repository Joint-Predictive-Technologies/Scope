# Contributing to Scope

This doc is primarily for the co-founder / technical collaborator. Two files are
required reading before you touch code:

1. **[`Scope/CLAUDE.md`](Scope/CLAUDE.md)** — engineering ground truth:
   conventions, the full rule table, scoring internals, database rules, and the
   two valid alert write paths. **This wins over every other doc on any factual
   conflict.**
2. **[`phase_zero_spec.md`](phase_zero_spec.md)** — the original product
   philosophy, user persona, and regulatory posture (kept as history; the product
   has grown well past its 10-rule plan).

For the roadmap and design decisions, see the knowledge vault:
[`vault/Scope/00_Index.md`](vault/Scope/00_Index.md) →
[Master Plan](vault/Scope/03_Roadmap/Master%20Plan.md).

---

## Where we are

Scope is a live, deployed system (Railway) — not a prototype. ~20 detection rules
run on an in-process APScheduler, writing scored alerts to SQLite that power a
FastAPI terminal UI (30+ pages), a daily Morning Brief, and an outcome-tracking
dataset. See [`CHANGELOG.md`](CHANGELOG.md) for how it got here and the
[README](README.md#current-status) for the current status table.

The active frontier is the **reasoning layer** on top of the outcome dataset
(regime recognition, historical analogues, structural permanence) — gated on the
calibration dataset maturing. See the
[Master Plan](vault/Scope/03_Roadmap/Master%20Plan.md).

---

## The one hard rule: scoring is human-gated

Anything that touches **scoring** (`insert_alert`, `enrich_scores`,
novelty/opportunity math), **corroboration** logic, **rule scripts**
(`rule_*.py`), **ingestion**, or **database/schema migrations** stays a manual,
human-in-the-loop change. These are the systems where signals can be silently
lost or miscounted, and detection-time scores are **immutable** — never
retroactively recomputed. Do not automate changes to them. (This is also why the
repo's agent setup deliberately has *no* scoring/dev subagent.)

Everything else — UI, docs, read-only diagnostics, infra hardening — is normal
collaborative work.

---

## Environment setup

```bash
cd Scope
pip install -r requirements.txt
cp .env.example .env
# Fill in GROQ_API_KEY at minimum. Most data sources need no key
# (GDELT, OpenSky, Polymarket, Federal Register, SEC EDGAR, PatentsView, FARA).
```

## Running

```bash
cd Scope
# Terminal (API + scheduler) on http://localhost:8000
uvicorn api.main:app --reload --port 8000

# Or run a single rule / ingestion pass:
python ingest_senate.py --since 2025-01-01 --emit-alerts
python scripts/rule_10_corroboration.py --emit-alerts
```

## Inspecting the database

Use a **read-only** connection for diagnostics so you don't trigger migrations or
backups:

```bash
cd Scope
sqlite3 'file:data/jpt.db?mode=ro'
> .tables
> SELECT headline, severity FROM alerts ORDER BY created_at DESC LIMIT 20;
> SELECT status, COUNT(*) FROM alert_outcomes GROUP BY status;
```

## Tests

```bash
cd Scope
python -m pytest tests/        # or: python3 tests/test_<name>.py
```

18 self-contained test modules. **Tests must pass before a commit.**

---

## Code conventions

- Match the surrounding style; don't introduce new frameworks. Reference code as
  `file_path:line`.
- **Alerts** have two accepted write paths (see `Scope/CLAUDE.md`): prefer
  `jpt_common.insert_alert()`, which computes scores inline at write time.
- **Migrations** are additive-only, tracked in `scope_migrations`. Never drop a
  table; guard column adds with `PRAGMA table_info`.
- **Commit/push only when asked.** End commit messages with the Co-Authored-By
  line. Don't rewrite working ingestion/scoring code without a specific reason —
  it's been verified against live data.

---

## Legal

Scope is a research and information-aggregation tool built on public filings and
open sources. It is not investment advice. See
[`phase_zero_spec.md`](phase_zero_spec.md) for the regulatory posture.
