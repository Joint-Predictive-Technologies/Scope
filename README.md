# Scope — Political-Market Intelligence Terminal

A research terminal that watches the machinery of government and money —
congressional trades, insider filings, lobbying, federal contracts, campaign
finance, patents, foreign-agent registrations, prediction markets, and open-source
intelligence — and surfaces **non-obvious, structurally-meaningful convergences**
the market has not yet priced.

When independent instruments of power move on the same target at the same time,
that convergence is a signal. Scope detects it, scores it on two independent axes,
explains why it matters, and then follows it forward to record what actually
happened next.

**You keep the judgment. Scope keeps the watch.**

> **Not investment advice.** Scope is an information-aggregation and research tool
> built on public filings and open sources. See [Legal](#legal).

---

## What Scope does today

This is a live, deployed system — not a plan. ~20 detection rules run on a
scheduler, writing scored, evidenced alerts to a database that powers a terminal
UI and a daily brief.

- **Detects** convergent activity across 13+ public data domains (see [Rules](#the-rules--live)).
- **Scores** every alert on two *independent* axes — **Evidence Confidence** (how
  well-supported) and **Opportunity** (how much edge remains after the market has
  absorbed it) — plus a **novelty** score that decays as a signal recurs.
- **Corroborates** — when 4+ *distinct* detection mechanisms converge on the same
  ticker within 24h, `RULE_10` fires a corroboration alert and builds a Market
  Thesis linking all the evidence.
- **Clusters** — `RULE_CLUSTER` detects 3+ congressional members trading the same
  ticker inside a 72h window (trade-proximity, accounting for the 30–45 day
  disclosure lag).
- **Explains** — an LLM (Groq) writes the "why this matters" narrative on the slow
  path. Never for prediction — only summarization, entity extraction, and context.
- **Briefs** — a scheduled **Morning Brief** (7 sections) summarizes the last 24h.
- **Remembers** — every alert is followed forward (+1 / +5 / +20 trading days,
  SPY-relative) into an `alert_outcomes` table. This compounding record of what
  the market did *after* Scope spoke is the product's long-term moat.

---

## Current status

| Area | Status |
|---|---|
| Congressional ingestion (House + Senate PTRs) | ✅ Live — full pipeline, PDF parsing, member matching |
| Detection rules (RULE_01B → RULE_15, OSINT/Reddit/ADS-B/Cluster/…) | ✅ ~20 live on the scheduler |
| Dual scoring (Evidence Confidence + Opportunity + novelty decay) | ✅ Live |
| Cross-source corroboration (RULE_10) | ✅ Live in production |
| Congressional cluster detection (RULE_CLUSTER) | ✅ Live in production |
| Daily Morning Brief | ✅ Live — scheduled 06:30 UTC |
| Outcome tracking / calibration dataset | ✅ Live — accumulating (labeling job runs daily) |
| Terminal UI (alerts, war rooms, congress, sectors, performance, …) | ✅ Live — 30+ pages |
| Deployment | ✅ Railway (FastAPI + in-process APScheduler) |

The reasoning layer that sits on top of the outcome dataset (regime recognition,
historical analogues, structural permanence) is the next frontier — see the
[long-term plan](vault/Scope/03_Roadmap/Master%20Plan.md) in the knowledge vault.

---

## The rules — live

Each rule is an independent detector run as a scheduled subprocess. Ground truth
for cadence and mechanics is [`Scope/CLAUDE.md`](Scope/CLAUDE.md).

| Rule | Domain | What it detects |
|---|---|---|
| `RULE_01B` | Congressional | First-touch on a new congressional PTR trade |
| `RULE_02` / `RULE_CLUSTER` | Congressional | Multiple members trading the same ticker (7-day / 72h windows) |
| `RULE_06` | Insider | SEC Form 4 insider-trade deviations |
| `RULE_07` | Prediction markets | Significant Polymarket position moves |
| `RULE_08` | Regulatory | Federal Register sector-impact rules |
| `RULE_09` | Regulatory | Lobbying-disclosure (LDA) spikes |
| **`RULE_10`** | **All** | **4+ distinct sources converging on one ticker — the corroboration engine** |
| `RULE_11` | Government | Federal contract awards (USASpending) |
| `RULE_12` | Foreign influence | FARA foreign-agent registrations |
| `RULE_13` | Campaign finance | FEC finance activity |
| `RULE_14` | Innovation | Patent grants (PatentsView) |
| `RULE_15` | Markets | Earnings-call NLP signals |
| `RULE_OSINT` | Geopolitical | GDELT event stream |
| `RULE_REDDIT` | Sentiment | Reddit activity (Arctic Shift) |
| `RULE_TELEGRAM_OSINT` | OSINT | Telegram channel monitoring |
| `RULE_ANOMALY` | Cross-cutting | Statistical anomaly detection |
| `RULE_ADSB` | OSINT | Aircraft-movement signals (OpenSky ADS-B) |
| `RULE_OPTIONS` | Markets | Options-flow correlation (enriches existing alerts) |

`RULE_10` is why the product exists: independent convergence of four different
detection mechanisms is structurally meaningful in a way any single signal is not.

---

## The scoring model

Two scores, **never merged**, both computed at detection time and **never
retroactively rewritten** (a score is a permanent record of what Scope believed
the moment it spoke — this is what makes the outcome dataset trustworthy).

- **Evidence Confidence** — maps distinct corroborating-source count to a
  confidence level; penalized when sources conflict.
- **Opportunity** — `novelty × 40 − absorption × 30 + horizon × 20 + win_rate × 10`,
  scaled by liquidity. Rewards novel, unabsorbed, longer-horizon signals. The
  `win_rate` term is a placeholder today, reserved for per-rule *realized* win
  rates once the outcome dataset is calibrated.
- **Novelty** — `1.0` for a first-ever signal, log-decaying with 30-day recurrence.

Full detail: [`Scope/CLAUDE.md`](Scope/CLAUDE.md) → *Scoring model*.

---

## Architecture at a glance

```
Public data sources ─► Ingestion + rule engine ─► SQLite (scored alerts)
                              │                          │
                       APScheduler (in-process)          ├─► FastAPI terminal UI
                       ~30 scheduled jobs                 ├─► Morning Brief / Telegram
                                                          └─► Outcome tracking (alert_outcomes)
```

- **FastAPI** app (`Scope/api/main.py`) serves the API and client-rendered static
  pages (vanilla JS + `fetch`, dark terminal aesthetic — no Jinja).
- **APScheduler** runs in-process as a background thread inside the web app — every
  rule and maintenance job is a scheduled subprocess. A universal failure safety
  net guarantees no scheduled-job failure (including import-time crashes) is silent.
- **SQLite** with additive, tracked migrations. `jpt_common.py` is the shared DB
  connection, scoring engine, and `insert_alert` write path.
- **Groq** for LLM narrative generation (slow path only), with a primary/fallback
  key chain.
- Deployed on **Railway**; hourly DB snapshots + a daily backup job.

Deeper technical reference: [`architecture.md`](architecture.md).

---

## Repository layout

```
Scope/                          # ← the application
├── api/
│   ├── main.py                 # FastAPI app + APScheduler wiring + page routes
│   ├── routers/                # JSON API routers (alerts, congress, themes, …)
│   └── static/                 # client-rendered terminal pages (30+)
├── jpt_common.py               # shared DB connection, scoring engine, insert_alert
├── ingest_senate.py            # Senate eFD PTR ingestion
├── ingest_house_index.py       # House disclosure index registration
├── parse_house_pdfs.py         # House PTR PDF parser (pdfplumber + OCR fallback)
├── rule_0X_*.py                # rule scripts (some at root, some in scripts/)
├── scripts/                    # rule scripts + jobs (enrich, backup, brief, outcomes…)
├── schema_sqlite.sql           # DB schema
├── tests/                      # 18 test modules (pytest-compatible)
├── CLAUDE.md                   # engineering ground truth — conventions & rule table
└── RESTORE.md                  # DB restore runbook

vault/                          # Obsidian knowledge base (plan, decisions, sessions)
└── Scope/
    ├── 00_Index.md             # start here
    └── 03_Roadmap/Master Plan.md   # the living long-term plan

README.md                       # this file
architecture.md                 # technical architecture reference
CONTRIBUTING.md                 # collaborator guide
phase_zero_spec.md              # original Phase-0 vision (historical)
CHANGELOG.md                    # what's been added over time
index.html                      # landing page
```

---

## Running locally

```bash
cd Scope
pip install -r requirements.txt
cp .env.example .env
# Fill in GROQ_API_KEY (and optionally FEC_API_KEY, etc.) in .env

# Start the terminal (API + scheduler) on http://localhost:8000
uvicorn api.main:app --reload --port 8000
```

Most data sources need **no API key** (GDELT, OpenSky, Polymarket, Federal
Register, SEC EDGAR, PatentsView, FARA). `GROQ_API_KEY` powers LLM narratives;
`FEC_API_KEY` (free) is needed for the full FEC bootstrap. See `.env.example`.

Running an individual rule / ingestion pass:

```bash
python ingest_senate.py --since 2025-01-01 --emit-alerts
python scripts/rule_10_corroboration.py --emit-alerts
```

---

## Tests

```bash
cd Scope
python -m pytest tests/            # or: python3 tests/test_<name>.py
```

18 self-contained test modules cover ingestion hardening, the scheduler safety
net, RULE_10 / RULE_CLUSTER, scoring, the morning brief, war rooms, and more.

---

## Documentation map

- **[`Scope/CLAUDE.md`](Scope/CLAUDE.md)** — engineering ground truth: conventions,
  the full rule table, scoring internals, database rules. *Start here to understand
  the code.*
- **[`architecture.md`](architecture.md)** — technical architecture reference.
- **[`vault/Scope/00_Index.md`](vault/Scope/00_Index.md)** — the knowledge vault:
  the [long-term Master Plan](vault/Scope/03_Roadmap/Master%20Plan.md), design
  decisions, and session history.
- **[`CHANGELOG.md`](CHANGELOG.md)** — what's been built, in order.
- **[`phase_zero_spec.md`](phase_zero_spec.md)** — the original vision (kept as
  history; the product has since grown well past it).

---

## Legal

Scope is an information-aggregation and research tool. It is **not investment
advice** and does not provide personalized recommendations. All data is sourced
from public filings and open sources. See [`phase_zero_spec.md`](phase_zero_spec.md)
for the regulatory posture.
