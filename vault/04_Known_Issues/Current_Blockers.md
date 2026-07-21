---
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
  decision (see below).

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

## Production Issues (from latest audit)

- **RULE_10 argparse contract:** Fixed AND merged to main (`fix/rule10-emit
  -alerts`, commit `6ea6a7a`). Confirmed live in production — clean hourly
  runs since deploy, 0 failures. Root cause: RULE_10 was broken for its
  *entire* scheduled lifetime (~13.5 days, 2026-07-07 onward) before this fix —
  100% failure rate, zero automatic corroboration alerts in that window. Not
  retroactively recoverable, but the exposure is now closed and documented.
- **Groq LLM fallback:** Implemented (`feat/llm-fallback`, pushed, not yet
  merged to main). `jpt_common.generate_narrative()` retries the primary Groq
  key twice, then falls back to a secondary key (`GROQ_API_KEY_FALLBACK`),
  logging `provider=primary|fallback|none` to `activity_log` every call.
  Verified live end-to-end against the real fallback key. **Remaining step:**
  add `GROQ_API_KEY_FALLBACK` to the Railway production environment — the code
  is deployed-ready but the env var only exists locally today.
- **`generate_brief.py` dead cron entry:** Fixed (`fix/remove-dead-generate
  -brief-job`, pushed). Was registered at the wrong path, 100% failure since
  2026-07-10 (~11 days) — superseded by `scripts/morning_brief.py`, so the
  entry was removed rather than path-fixed.
- **Disk usage at 92%:** Fixed (resized to 5GB). *(Not independently verified
  this session — carried over from a prior audit.)*
- **pdfplumber / pillow missing:** Fixed (added to requirements.txt).

---

See also: [[Roadmap Tracking]], CLAUDE.md Known Issues section
