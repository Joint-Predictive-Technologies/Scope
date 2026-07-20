# Database Restore Procedure

How to restore Scope's SQLite database from a backup. **Dry-run this against a
local copy before you ever trust it in a real emergency** — do not first learn
this procedure during an incident.

## Backup inventory

Two kinds of backup exist under `data/backups/`:

| File pattern | Made by | Method | Trust |
|---|---|---|---|
| `snapshot_YYYYMMDD_HHMM.db.gz` | `scripts/db_backup.py` (daily 03:00 UTC) | SQLite **online backup API** + `PRAGMA integrity_check` + gzip | **Preferred** — verified consistent |
| `jpt_YYYYMMDD_HHMM.db` | `jpt_common._backup_db` (hourly, on DB connect) | raw `shutil.copy2` | Fallback only — a raw copy can catch a half-written page; not integrity-checked |

> ⚠️ **Same-volume caveat.** Both live on the same Railway volume as the primary
> DB — the same failure domain. They protect against *logical* corruption / bad
> migrations / accidental deletes, **not** a volume loss. Off-volume protection
> requires the remote upload in `db_backup.upload_remote()`, which is dormant
> until `BACKUP_S3_*` credentials are configured (see below).

## Restore from a `snapshot_*.db.gz` (preferred)

Run from the app root (the directory containing `data/`). Never overwrite the
live DB before verifying the backup.

```bash
# 1. Pick a backup (newest listed last)
ls -t data/backups/snapshot_*.db.gz

# 2. Decompress to a scratch file (keep the .gz intact)
BK=data/backups/snapshot_20260720_1853.db.gz
gunzip -kc "$BK" > /tmp/restore_candidate.db

# 3. VERIFY before trusting it — must print exactly "ok"
sqlite3 /tmp/restore_candidate.db "PRAGMA integrity_check;"

# 4. Sanity-check it isn't empty / truncated (expect non-zero, plausible counts)
sqlite3 /tmp/restore_candidate.db \
  "SELECT 'alerts', COUNT(*) FROM alerts
   UNION ALL SELECT 'transactions', COUNT(*) FROM transactions
   UNION ALL SELECT 'alert_outcomes', COUNT(*) FROM alert_outcomes;"

# 5. Take the app out of service (stop the process / pause the Railway service).

# 6. Preserve the current (suspect) DB first — never delete it outright.
mv data/jpt.db data/jpt.db.broken.$(date -u +%Y%m%d_%H%M)

# 7. Put the verified backup in place.
cp /tmp/restore_candidate.db data/jpt.db

# 8. Restart the app. On boot, db_connection() runs idempotent migrations;
#    they should be no-ops on a current backup. Confirm the app serves and
#    /api/stats returns sane counts, then delete the .broken file once satisfied.
```

## Restore from an `jpt_*.db` hourly copy (fallback)

Same as above but skip the gunzip (it's already a `.db`), and **still run the
integrity_check in step 3** — these are unverified raw copies, so a check is
even more important. Prefer the newest `snapshot_*.db.gz` whenever one exists.

## Remote restore (once `BACKUP_S3_*` is configured)

When object storage is provisioned, backups are uploaded to
`scope-db-backups/snapshot_*.db.gz`. To restore:

```bash
# download the chosen object to data/backups/ (via the provider CLI or boto3),
# then follow the snapshot restore steps above from step 2.
```

## Provisioning remote storage (currently NOT configured)

`db_backup.py` writes locally today and **skips** remote upload because no store
is configured. To enable off-volume backups, set these env vars (Backblaze B2 or
Cloudflare R2 are cheap S3-compatible options) and add `boto3` to
`requirements.txt`:

```
BACKUP_S3_ENDPOINT           # e.g. https://s3.us-west-004.backblazeb2.com  (B2)
                             #   or  https://<accountid>.r2.cloudflarestorage.com (R2)
BACKUP_S3_BUCKET             # bucket name, e.g. scope-db-backups
BACKUP_S3_ACCESS_KEY_ID
BACKUP_S3_SECRET_ACCESS_KEY
```

No code change is needed beyond adding `boto3` — `upload_remote()` already reads
these and uploads on the next scheduled run.
