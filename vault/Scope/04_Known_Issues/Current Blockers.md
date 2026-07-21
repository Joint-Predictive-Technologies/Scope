---
aliases: [Current Blockers, Known Issues]
type: issue
stage: iPhone-1
status: active
priority: critical
tags: [blockers, production, infrastructure]
related: [[In-Flight Sessions]], [[Roadmap Tracking]]
---

# Current Blockers and Open Items

Issues actively blocking progress or needing a human decision.

## Infrastructure

- **Database backups:** Automated locally (verified compressed daily snapshot,
  integrity-checked, tiered retention — `scripts/db_backup.py`, `feat/db-backup
  -automation`, merged to main). No remote/off-volume storage yet — still the
  single biggest residual risk, since local backups share the same Railway
  volume as the primary DB (same failure domain). Blocked on a cloud storage
  decision (see below). **Restore procedure verified 2026-07-21** — both the
  preferred `snapshot_*.db.gz` and the fallback raw copy restore to a complete,
  integrity-checked DB via `RESTORE.md` (tested against a scratch copy; live DB
  untouched). So the residual risk is now purely the *missing off-volume copy*,
  not an unproven restore path.

## Decisions Pending

- **Theme Temperature architecture:** Circularity guard design. Deferred until
  a joint design session. (SCOPE_IPHONE15_VISION.md, Layer 2)

- **Cloud storage provider:** Backups automation needs remote credentials
  (Backblaze B2, Cloudflare R2, or other S3-compatible store). User has opted
  to provision this themselves; `scripts/db_backup.py`'s `upload_remote()` is
  already storage-ready — it activates the moment `BACKUP_S3_ENDPOINT` /
  `BACKUP_S3_BUCKET` / `BACKUP_S3_ACCESS_KEY_ID` / `BACKUP_S3_SECRET_ACCESS_KEY`
  are set and `boto3` is added to requirements.txt. No code change needed,
  just the credentials.

## Awaiting review / merge (complete, not merged)

Confirmed by the 2026-07-21 reconciliation: both branches are complete,
pushed, and in sync with origin. Not merged — only `fix/rule10-emit-alerts`
was ever pre-approved (and it is already merged). These wait for review:

- **`feat/llm-fallback`** (`9f77654`) — Groq primary/fallback narrative
  generation. Ready.
- **`fix/remove-dead-generate-brief-job`** (`d3687eb`) — removes the dead
  `generate_brief.py` cron entry. Ready.

## Open action items (no code — production config)

- **Add `GROQ_API_KEY_FALLBACK` to the Railway production environment.** The
  fallback code is deployed-ready but the env var only exists in the local
  `.env` today, so the secondary provider is inert in prod until this is set.
  (Depends on merging `feat/llm-fallback` first.)

## Resolved (kept for the audit trail)

- **RULE_10 argparse contract — RESOLVED 2026-07-20.** Fixed AND merged to
  main (`fix/rule10-emit-alerts`, commit `6ea6a7a`); confirmed live in
  production — clean hourly runs since deploy, 0 failures. Root cause: RULE_10
  was broken for its *entire* scheduled lifetime (~13.5 days, 2026-07-07
  onward) before this fix — 100% failure rate, zero automatic corroboration
  alerts in that window. Not retroactively recoverable, but the exposure is
  now closed and documented.
- **Groq LLM fallback — IMPLEMENTED, awaiting merge.** `jpt_common
  .generate_narrative()` retries the primary Groq key twice, then falls back
  to a secondary key (`GROQ_API_KEY_FALLBACK`), logging
  `provider=primary|fallback|none` to `activity_log` every call. Verified live
  end-to-end against the real fallback key. On `feat/llm-fallback` (see
  "Awaiting review" above); prod env var still needed (see "Open action
  items").
- **`generate_brief.py` dead cron entry — FIXED, awaiting merge.** Was
  registered at the wrong path, 100% failure since 2026-07-10 (~11 days) —
  superseded by `scripts/morning_brief.py`, so the entry was removed rather
  than path-fixed. On `fix/remove-dead-generate-brief-job`.
- **Disk usage at 92% — reported fixed (resized to 5GB).** *(Not independently
  verified this session — carried over from a prior audit.)*
- **pdfplumber / pillow missing — FIXED** (added to requirements.txt, merged).

---

See also: [[Roadmap Tracking]], CLAUDE.md Known Issues section
