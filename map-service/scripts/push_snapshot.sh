#!/usr/bin/env bash
# Ship the read-only serving snapshot to the map service's Railway volume.
#
# 🔴 THIS IS THE ONLY WRITE PATH INTO THE MAP SERVICE'S DATA, AND IT IS MANUAL ON
# PURPOSE.  The service itself has no write route, no upload endpoint and no admin
# surface — adding one so the snapshot could be refreshed over HTTP would put a
# write path on a public box to save a two-minute command.
#
# ⚠️ IT IS ALSO NOT SCOPE'S DEPLOY PIPELINE.  The snapshot is this service's own
# responsibility: it is rebuilt from `osint.db` when the graph changes, not when
# Scope ships. Nothing about a Scope deploy refreshes it and nothing about
# refreshing it touches Scope.
#
#   ./scripts/push_snapshot.sh <local-snapshot.db> <project-id> <service-id> <env-id>
#
# Measured: 624 MB -> 162 MB with `gzip -1` (26%, 3.6s), and ~1.4 MB/s through
# `railway ssh`, so about two minutes wall clock. `gzip -1` rather than -9 because
# the transfer, not the compression, is the bottleneck.
set -euo pipefail

SNAP="${1:?usage: push_snapshot.sh <snapshot.db> <project> <service> <env>}"
PROJECT="${2:?}"; SERVICE="${3:?}"; ENVIRONMENT="${4:?}"
REMOTE="${REMOTE_PATH:-/app/data/osint-map-serving.db}"

[ -f "$SNAP" ] || { echo "no such snapshot: $SNAP" >&2; exit 1; }

rw() { railway ssh -p "$PROJECT" -s "$SERVICE" -e "$ENVIRONMENT" "$@"; }

LOCAL_MD5=$(md5 -q "$SNAP" 2>/dev/null || md5sum "$SNAP" | cut -d' ' -f1)
BYTES=$(stat -f%z "$SNAP" 2>/dev/null || stat -c%s "$SNAP")
echo "  local  : $SNAP"
echo "           $BYTES bytes, md5 $LOCAL_MD5"

# 🔴 CHECK THE FILE IS SOUND BEFORE SHIPPING IT, not after.  A torn or truncated
# SQLite file is still a file; it fails at the first query instead of at upload.
python3 - "$SNAP" <<'PY'
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "integrity_check FAILED"
n = con.execute("SELECT COUNT(*) FROM _serving_snapshot").fetchone()[0]
assert n, "no _serving_snapshot manifest — is this really a serving snapshot?"
print(f"           integrity_check ok, manifest present ({n} rows)")
PY

# ⚠️ UPLOAD TO A TEMPORARY NAME AND MOVE INTO PLACE.  Streaming straight onto the
# live path would leave a half-written database being read by the running service
# for the two minutes the transfer takes.  The move is atomic within one volume.
echo "  upload : streaming gzip -> $REMOTE.incoming"
gzip -1 -c "$SNAP" | rw "gzip -dc > '$REMOTE.incoming'"

echo "  verify : md5 on the container"
REMOTE_MD5=$(rw "md5sum '$REMOTE.incoming'" | tr -d '\r' | awk '{print $1}' | tail -1)
if [ "$REMOTE_MD5" != "$LOCAL_MD5" ]; then
  echo "  🔴 MD5 MISMATCH — local $LOCAL_MD5, remote $REMOTE_MD5. Leaving .incoming in place; NOT swapping." >&2
  exit 1
fi
echo "           $REMOTE_MD5  matches"

rw "python3 -c \"import sqlite3;c=sqlite3.connect('file:$REMOTE.incoming?mode=ro',uri=True);print('           remote integrity_check',c.execute('PRAGMA integrity_check').fetchone()[0])\""
rw "mv '$REMOTE.incoming' '$REMOTE' && ls -l '$REMOTE'"
echo "  done   : restart the service (or let the next deploy pick it up) so the"
echo "           process reopens the file — the handle is cached for the process lifetime."
