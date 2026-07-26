---
type: session-summary
stage: iPhone-1
status: completed
priority: critical
tags: [session, work-log, ws1, merge, deploy, incident, data-recovery]
related: [[SESSION-2026-07-26-ws1-merge-check]], [[SESSION-2026-07-26-ws1-completion]]
date-created: 2026-07-26
---

# Session: WS1 merged and pushed — with a DB-deletion incident

**Date:** 2026-07-26
**Branch:** `fix/test-isolation-and-untrack-db` → `main` (fast-forward)
**Status:** Completed. Merged and pushed on explicit human instruction.

## Goal

Merge WS1 into `main` and push to `origin`, as instructed.

## Outcome

Done. `main` = `origin/main` = `b71e71d`. Suite green on `main` (134/134).

**⚠️ The merge deleted the working database. It was recovered intact.** Details
below — this is the important part of this note.

---

## ⚠️ Incident: `Scope/data/jpt.db` was deleted by the merge

**What happened.** On `main`, `Scope/data/jpt.db` was a **tracked** file. WS1
contains the commit that runs `git rm --cached` on it. Fast-forwarding `main` to
that commit removed the path from the index — and because git had been tracking
the file on `main`, it also **deleted the working-tree file**, then removed the
now-empty `Scope/data/` directory.

```
$ git merge fix/test-isolation-and-untrack-db      # fast-forward, no conflict
$ ls -la Scope/data/jpt.db
ls: Scope/data/jpt.db: No such file or directory
```

**Why it was not predicted.** The merge-readiness check
([[SESSION-2026-07-26-ws1-merge-check]]) flagged that "any other clone that pulls
this will lose its copy of the database from git". That was right but incomplete:
it framed the loss as affecting *other* machines. It did not follow through to the
obvious corollary — **this machine's `main` also tracked the file**, so the merge
would delete it here too. `git rm --cached` preserves the working file *on the
branch where it runs*; it does not protect the file when that deletion is merged
into a branch that still tracks it.

**Recovery.** The content was still in git history. Restored from the commit
immediately before the merge:

```bash
mkdir -p Scope/data
git show 4003511:Scope/data/jpt.db > Scope/data/jpt.db
```

**Verified intact — nothing lost:**

| Check | Result |
|---|---|
| SHA-256 | `8cf8f41c7699fd00237ae472941301276693081d12c04c4d5518efc4ddfbe4e4` — identical to the pre-merge checksum recorded throughout WS1 |
| Size | 5,750,784 bytes |
| `PRAGMA integrity_check` | `ok` |
| Tables | 37 |
| `alerts` rows | 3,347 |
| `alerts` `sqlite_sequence` | 8926 |
| `activity_log` rows | 312 |

No data was lost because the working file was already byte-identical to the
committed version — WS1 never wrote to it, and that was verified by checksum at
every step. **Had the working DB diverged from the last commit, that divergence
would have been destroyed**, recoverable only from `Scope/data/backups/` (which
does not exist locally) or the Railway volume.

**End state is correct:** the file is on disk, untracked, and ignored via
`.gitignore:24` (`Scope/data/*.db`). Future git operations can no longer touch it —
which is precisely the class of corruption WS1 existed to stop.

## What this means for anyone else

Any other clone with `jpt.db` currently tracked will lose its working copy on the
next `git pull`. Before pulling, they should copy it aside:

```bash
cp Scope/data/jpt.db ~/jpt.db.backup && git pull
```

**Production is unaffected.** Railway sets `DATABASE_PATH=/app/data/jpt.db` and
reads the persistent volume, never the repo copy (C5, human-confirmed via the
Railway dashboard 2026-07-26; not independently verified by Claude Code, which
cannot reach production).

## Merge and push record

```
$ git merge fix/test-isolation-and-untrack-db
Fast-forward (no merge commit)

$ git push origin main
To github.com:Joint-Predictive-Technologies/Scope.git
   4003511..b71e71d  main -> main

$ git rev-list --left-right --count origin/main...main
0	0                                  # in sync
```

- `main` = `origin/main` = `b71e71d`, fast-forward from `4003511`.
- **11 commits** landed: the WS1 code plus the four earlier documentation commits
  (generic-ticker diagnosis, RULE_10 convergence trace, gate-reachability verdict,
  and both decision records) — WS1 was branched from `trace/rule10-convergence`.
- `Scope/data/jpt.db` is now absent from `origin/main` (`git ls-tree` returns
  nothing), as intended.
- The six subagents are now tracked and shared.

**Post-merge verification on `main`:** suite **134 passed**; DB checksum unchanged
after the run.

## What shipped to production

The push is what reaches Railway. Landing in prod:

- Test isolation (`conftest.py`, the `_get_db_path` test guard) — no runtime effect.
- `api/main.py` path resolution via `_get_db_path(None)` — a **no-op on Railway**,
  since `DATABASE_PATH` is set and short-circuits before the changed branch.
- `jpt.db` no longer in the repo — prod reads the volume, so no effect.
- Docs, vault notes, `requirements-dev.txt`, agent files — no runtime effect.

**Expected production impact: none.** Watch the Railway deploy to confirm it comes
up clean.

## Findings — FLAGGED FOR LATER

- **F1.** `Scope/data/backups/` does not exist locally, so `_backup_db`'s hourly
  copy has never run in this working tree. Had the DB diverged from the last
  commit, there would have been no local fallback. Worth creating deliberately.
- **F2.** The four stale branches (`fix/test-isolation-and-untrack-db`,
  `trace/rule10-convergence`, `diagnose/generic-ticker-surfacing`) are now fully
  contained in `main` and can be deleted. Not done — branch deletion was not asked
  for.

## Provenance

Every command output above is pasted from this machine. The recovery checksum was
compared against the value recorded independently at the start of WS1 and at each
subsequent verification. Production was not reached.

## Next

WS2 — RULE_06 reliability. Restart the session first so the now-tracked `verifier`
subagent loads.

---

### Related

[[SESSION-2026-07-26-ws1-merge-check]], [[SESSION-2026-07-26-ws1-completion]],
[[SESSION-2026-07-26-test-isolation]], [[2026-07-25-gate-redesign]]
