#!/usr/bin/env bash
# Appends one breadcrumb to the vault session log at the end of every session.
#
# WHY THIS EXISTS: _session-log.md documented a "SessionEnd hook in
# .claude/settings.json" that had NEVER BEEN CREATED — the file did not exist, so
# the hook never fired and the log sat empty all week while its own header claimed
# it was auto-appended. That is why sessions went terminal-only.
#
# Deliberately defensive: a hook that errors can disrupt the session, so every step
# is guarded and the script always exits 0.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG="$REPO/vault/Scope/02_Sessions/_session-log.md"
[ -f "$LOG" ] || exit 0

TS="$(date -u '+%Y-%m-%d %H:%M UTC')"
# 2>/dev/null AND the empty-guard both matter: in a repo with ZERO commits,
# `rev-parse --abbrev-ref HEAD` prints "HEAD" to stdout *and* exits 128, so a bare
# `|| echo '?'` yields BOTH and the markdown header breaks across two lines.
BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null | head -1 || true)"
[ -n "$BRANCH" ] || BRANCH='?'
COMMITS="$(git -C "$REPO" log --since='6 hours ago' --oneline 2>/dev/null | head -12)"
N="$(printf '%s' "$COMMITS" | grep -c . || true)"

{
  printf '\n## %s — `%s`\n' "$TS" "$BRANCH"
  if [ "${N:-0}" -gt 0 ]; then
    printf '%s commit(s) in the last 6h:\n\n' "$N"
    printf '%s\n' "$COMMITS" | sed 's/^/  - /'
  else
    printf 'No commits in the last 6h (read-only or discussion session).\n'
  fi
} >> "$LOG" 2>/dev/null

exit 0
