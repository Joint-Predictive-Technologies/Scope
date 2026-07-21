---
type: roadmap
stage: iPhone-5
status: active
priority: high
tags: [roadmap, planning]
related: [[iPhone Stage Progress]], [[Known Issues]]
---

# Active Roadmap Tracking

What's in flight, what's up next, dependencies.

## Recently Completed (this bulk-session arc)

- RULE_10 `--emit-alerts` fix — merged, confirmed live in production
- RULE_02 scheduling + ingest_senate hardening — merged
- Database backup automation (local interim) — merged (`feat/db-backup
  -automation`); remote upload storage-ready, pending credentials
- Groq multi-provider fallback — implemented (`feat/llm-fallback`, pushed,
  not yet merged; needs `GROQ_API_KEY_FALLBACK` added to Railway prod)
- Congressional digest standalone view (`/congress/digest/<date>`) — merged
- `generate_brief.py` dead cron entry — removed (`fix/remove-dead-generate
  -brief-job`, pushed, not yet merged)
- Production audit sweep (argparse contract, silent failures, scheduler
  reconciliation) — completed, findings documented in [[Current Blockers]]

## Queued (Next Priority)

1. Merge the open branches above (none merged to main without explicit
   approval per this project's convention)
2. Add `GROQ_API_KEY_FALLBACK` to Railway production env
3. Get remote backup storage credentials, wire up `boto3` + `upload_remote()`
4. Theme Temperature design session (pending)
5. Continue outcome tracking calibration (passive, clock-ticking)
6. RULE_PHARMA design and implementation

## Deferred / Waiting On

- Overwatch vs Scanner mode navigation split (design-first, not code-first)
- Regime recognition layer (needs more outcome data)
- Historical analogues (ditto)

## Risks

- **Database backup — remote storage:** Local interim backup closes most of
  the gap, but no off-volume copy exists yet (same failure domain as the
  primary DB). No production restore has been tested end-to-end. This is
  still the single biggest residual risk.
- **Outcome data calibration:** Takes time. Can't accelerate meaningfully.

---

See also: [[iPhone Stage Progress]], [[Current Blockers]]
