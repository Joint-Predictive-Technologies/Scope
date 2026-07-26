---
type: session-summary
stage: iPhone-1
status: completed
priority: critical
tags: [session, work-log, ws1, merge-readiness, read-only, human-gated]
related: [[SESSION-2026-07-26-ws1-completion]], [[SESSION-2026-07-26-test-isolation]]
date-created: 2026-07-26
---

# Session: WS1 merge-readiness check

**Date:** 2026-07-26
**Branch inspected:** `fix/test-isolation-and-untrack-db`
**Status:** Completed. **Read-only — nothing merged, nothing pushed.**

## Goal

Determine whether WS1 is already merged into `main`; if not, run every read-only
merge-readiness check, surface the diff, and hand the human the exact commands.

## Outcome

**NOT MERGED.** All gates green. The branch is ready for a human merge, with two
things the human should know first — see §5.

---

## 1. Merged status — NOT MERGED

```
$ git merge-base --is-ancestor fix/test-isolation-and-untrack-db main && echo MERGED || echo "NOT MERGED"
NOT MERGED

$ git branch --merged main
  main
```

`main` is at `4003511`; the branch head is `8d9941f`.

## 2. Diff and forbidden-path check

**11 commits** ahead of `origin/main` — more than WS1 alone, see §5.

```
$ git diff main...fix/test-isolation-and-untrack-db --stat
 .claude/agents/data-integrity.md                   | 134 ++++++
 .claude/agents/diff-gatekeeper.md                  | 133 ++++++
 .claude/agents/provenance-guardian.md              | 113 +++++
 .claude/agents/scheduler-reliability.md            | 121 ++++++
 .claude/agents/signal-scoring.md                   | 155 +++++++
 .claude/agents/verifier.md                         | 133 ++++++
 .gitignore                                         |  20 +-
 Scope/CLAUDE.md                                    |   2 +-
 Scope/api/main.py                                  |  12 +-
 Scope/data/jpt.db                                  | Bin 5750784 -> 0 bytes
 Scope/jpt_common.py                                |  36 ++
 Scope/requirements-dev.txt                         |   9 +
 Scope/tests/conftest.py                            |  52 +++
 Scope/tests/test_influence_entity.py               |  75 +++-
 Scope/tests/test_war_rooms.py                      |  84 +++-
 vault/Scope/00_Index.md                            |   8 +
 ... 7 session notes, 2 decision records, Current Blockers,
     generic_ticker_surfacing_diagnosis ...
 27 files changed, 3348 insertions(+), 31 deletions(-)
```

**Forbidden-path check — clean, verified three ways:**

1. **Filenames:** no `rule_*.py`, no `enrich_scores`, no
   `rule_10_corroboration`, no `rule_cluster`, no `schema_sqlite.sql`.
2. **`Scope/jpt_common.py` hunks** (the file that *hosts* scoring and migrations,
   so filename alone proves nothing): **purely additive, zero deleted lines** —
   `import sys`, `_running_under_test()`, and the test guard inside
   `_get_db_path`. No scoring function and no migration block touched.
3. **Whole-diff string sweep** over Python source for
   `scope_migrations`, `ALTER TABLE`, `DROP TABLE`, `DELETE FROM`,
   `calculate_opportunity_score`, `calculate_evidence_confidence`,
   `calculate_novelty_score`, `enrich_alert_scores`, `insert_alert`: **all zero.**
   `_initialize_schema` returned **1** — traced to prose inside a docstring
   (`Scope/tests/conftest.py:40`), not code.

`Scope/api/main.py` is 3 identical two-line substitutions replacing a
hand-built path with `_get_db_path(None)`.

> **Note on the brief's expected-file list.** It named "conftest, `.gitignore`,
> `.claude/agents/*.md`, vault notes, plus the untracking of `Scope/data/jpt.db`".
> The real diff also contains `Scope/api/main.py`, `Scope/jpt_common.py`,
> `Scope/requirements-dev.txt`, `Scope/CLAUDE.md` and two test files. All are
> genuine WS1 work documented in [[SESSION-2026-07-26-test-isolation]] and
> [[SESSION-2026-07-26-ws1-completion]] — the brief's list was incomplete, not the
> diff.

## 3. Green gate

```
$ pytest tests/ -q          # clean process, no env vars
134 passed, 9 warnings in 5.23s

$ git ls-files --error-unmatch Scope/data/jpt.db
error: pathspec 'Scope/data/jpt.db' did not match any file(s) known to git   ✓ untracked

$ git check-ignore -v Scope/data/jpt.db
.gitignore:24:Scope/data/*.db	Scope/data/jpt.db                              ✓ ignored

$ shasum -a 256 Scope/data/jpt.db
8cf8f41c7699fd00237ae472941301276693081d12c04c4d5518efc4ddfbe4e4

$ ls -la Scope/data/jpt.db
-rw-r--r--  1 sapper  staff  5750784 Jul 24 15:56   ✓ still on disk, 5.75 MB
```

## 4. Remote and deploy context

```
$ git remote -v
origin  git@github.com:Joint-Predictive-Technologies/Scope.git (fetch/push)

$ git rev-list --left-right --count origin/main...main
0	0                       # main and origin/main are identical (4003511)
```

`main` is **not** ahead of `origin/main` — they are the same commit, so nothing is
waiting to be pushed *yet*. The 11 commits sit only on the WS1 branch.

Because `main` is an ancestor of the branch, the merge would be a
**fast-forward** — no merge commit needed.

**Railway caveat.** Railway deploys from the **remote**, not this machine. Merging
locally changes nothing in production; **the push is what ships WS1** — including
the change in how `jpt.db` is tracked and the six now-shared agents. Since this
commit changes DB tracking, confirm the Railway deploy source (branch and repo)
before pushing.

The C5 finding says this is safe: `DATABASE_PATH=/app/data/jpt.db` is set in
Railway, so production reads the volume and never the repo copy. That fact is
**human-confirmed via the Railway dashboard on 2026-07-26 and was not
independently verified by Claude Code**, which cannot reach production.

## 5. Two things to know before merging

1. **The branch carries 11 commits, not just WS1.** WS1 was branched from
   `trace/rule10-convergence` rather than `main`, so it also brings the
   generic-ticker diagnosis, the RULE_10 convergence trace, the gate-reachability
   verdict, and both decision records. All are documentation except the WS1 code
   itself. Merging WS1 therefore also lands those four earlier doc commits — which
   is probably desirable, since they are the record WS2 builds on, but it should be
   a deliberate choice rather than a surprise. `trace/rule10-convergence` and
   `diagnose/generic-ticker-surfacing` would become redundant afterwards.
2. **`Scope/data/jpt.db` is deleted from the index by this merge** (`Bin 5750784
   -> 0 bytes`). The file stays on disk here, but **any other clone that pulls
   this will lose its copy of the database from git** and must rely on its own
   local file or the Railway volume. That is the intended behaviour, and it is the
   single most consequential line in the diff.

## 6. Exact commands for the human

```bash
# 1. Review the full diff first
git diff main...fix/test-isolation-and-untrack-db

# 2. Merge (fast-forward — no merge commit needed)
git checkout main
git merge fix/test-isolation-and-untrack-db

# 3. Push — ONLY after confirming the Railway deploy source
git push origin main
```

Optional cleanup once merged and pushed, since both are then contained in `main`:

```bash
git branch -d trace/rule10-convergence
git branch -d diagnose/generic-ticker-surfacing
```

## 7. Closing status table

| Item | Status |
| ---- | ------ |
| WS1 merged into main? | **NOT MERGED** — PROVEN: `merge-base --is-ancestor` returned non-zero; `git branch --merged main` lists only `main` |
| Diff is the expected WS1 set; no forbidden path | PROVEN (caveat) — no rule/scoring/corroboration/migration change, verified by filename, by `jpt_common.py` hunk inspection (additive only, 0 deletions) and by a 10-string sweep (all 0; the one `_initialize_schema` hit is docstring prose). **Caveat:** the diff is wider than the brief's list — 4 extra doc commits and 4 extra source files, all documented WS1 work |
| Suite green on branch (134/134) | PROVEN — `134 passed` from a clean process with no env vars |
| `jpt.db` untracked; checksum recorded | PROVEN — `git ls-files --error-unmatch` errors; ignored via `.gitignore:24`; `8cf8f41c…`; 5,750,784 bytes still on disk |
| Remote/push state reported | PROVEN — `origin` is `Joint-Predictive-Technologies/Scope`; `main` == `origin/main` (`4003511`), 0/0 ahead; merge would fast-forward; Railway caveat recorded |
| Merge + push left to the human | PROVEN — no `git merge`, `git push`, `git reset`, or checkout was run; only read-only inspection and the test suite |

## Provenance

All output above is pasted from commands run on this machine against branch
`fix/test-isolation-and-untrack-db`, using three-dot diffs that require no
checkout. The working DB was never written to — the suite runs redirected through
`tests/conftest.py`, and its checksum is unchanged. Production was not reached;
the Railway `DATABASE_PATH` fact is human-reported.

## Human-gated

The merge to `main` and the push to `origin` are the human's act. This session
changed nothing.

---

### Related

[[SESSION-2026-07-26-ws1-completion]], [[SESSION-2026-07-26-test-isolation]],
[[2026-07-25-gate-redesign]]
