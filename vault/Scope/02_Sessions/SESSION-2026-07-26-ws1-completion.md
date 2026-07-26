---
type: session-summary
stage: iPhone-1
status: completed
priority: critical
tags: [session, work-log, ws1, test-isolation, git-hygiene, subagents, human-gated]
related: [[SESSION-2026-07-26-test-isolation]], [[2026-07-25-subagent-roster]], [[Current Blockers]]
date-created: 2026-07-26
---

# Session: WS1 completion — close the knob, the blocker, and the roster gap

**Date:** 2026-07-26
**Branch:** `fix/test-isolation-and-untrack-db`
**Status:** Completed. **WS1 is unblocked and ready for human merge. Not merged.**

## Goal

Finish WS1 so it merges whole and green: delete the broken `SCOPE_TEST_SEED_DB`
mode, record the C5 resolution the human confirmed, make the six subagents
shippable, and put the completion report in the vault rather than leaving it in
the terminal.

## Outcome

Done. Suite is **134/134** on the sole supported path, both original WS1 blockers
(C4, C5) are closed, and the agent roster is now trackable in git.

---

## 1. What changed, per file

| File | Change |
|---|---|
| `Scope/tests/conftest.py` | Removed the `SCOPE_TEST_SEED_DB` knob: the env branch, the `_SEED_DB` module constant, the `shutil.copyfile` seed step, the now-unused `shutil` import, and the docstring line. **The per-test temp-DB isolation mechanism is untouched.** |
| `.gitignore` | Replaced the blanket `.claude/` exclusion with a targeted negation so `.claude/agents/*.md` are trackable while everything else under `.claude/` stays ignored. |
| `vault/Scope/02_Sessions/SESSION-2026-07-26-test-isolation.md` | C5 marked **RESOLVED** with provenance; the SEED_DB limitation marked **resolved by deletion**; the blocker list updated. |
| `vault/Scope/04_Known_Issues/Current Blockers.md` | The two WS1-fixed open questions (tests mutating prod DB; `jpt.db` tracked in git) struck through and marked fixed. |
| `.claude/agents/*.md` (6 files) | Newly trackable. Contents unchanged. |

No `rule_*.py`, scoring, `enrich_scores`, corroboration, `jpt_common.py`,
`Scope/api/`, or `scripts/` path appears in the diff — verified with
`git diff HEAD --name-only`. No migrations.

The `.gitignore` pattern, since git cannot re-include a file whose parent
directory is excluded:

```gitignore
.claude/*
!.claude/agents/
.claude/agents/*
!.claude/agents/*.md
.claude/settings.local.json
.claude/*.local.json
```

## 2. Full-suite result

**134 passed**, clean process, no environment variables set, fresh empty DB per
test. Working DB byte-identical before and after
(`8cf8f41c7699fd00237ae472941301276693081d12c04c4d5518efc4ddfbe4e4`).

The decisive check that the knob is truly gone: setting `SCOPE_TEST_SEED_DB`
anyway now yields **134/134** rather than the previous 131/134 — the variable is
inert, not merely unused by default.

## 3. C5 record

Recorded in [[SESSION-2026-07-26-test-isolation]] as **✅ C5 RESOLVED — 2026-07-26**:
`DATABASE_PATH=/app/data/jpt.db` is set in Railway, so untracking `jpt.db` is safe
(production reads the volume, never the repo copy) and the `api/main.py` path fix
is a no-op there. The predicted failure — `_hours_since_last_alert()` returning
`inf` and running every rule on every deploy boot — would not have occurred.

> **Provenance:** human-confirmed via the Railway dashboard, 2026-07-26. **Not
> independently verified by Claude Code**, which cannot reach production from this
> environment. A reported observation, not a re-derived one.

## 4. Agent tracking + no-secrets check

Scanned all six agent files before making them trackable:

- **Secrets/tokens/keys:** three regex hits, all false positives — "token-wise"
  ×2 (ticker string tokenisation) and "no Railway CLI, no credentials" (a sentence
  asserting their absence). **No real secret material.**
- **Absolute machine paths** (`/Users/`, `/home/`, `/private/tmp`, `C:\`): none.
- **Emails / personal identifiers:** none.

`git add -n .claude/` stages **exactly six `.md` files and nothing else**.
Simulated machine-local files (`settings.local.json`, `settings.json`,
`history.jsonl`, `todos/*.json`) all remain ignored.

## 5. Verifier pass

The `verifier` subagent was **not** invoked — it was added earlier today and
file-based agents load only at session start, so it is not available in this
session. An explicit second pass was run instead:

| Check | Result |
|---|---|
| V2 full suite, clean process, fresh empty DB | 134 passed |
| V1/V3 working-DB checksum before/after | identical |
| V4 `SEED_DB` absent from `tests/`, `api/`, root `*.py` | absent |
| V5 old env var set anyway | inert — still 134/134 |
| V6 isolated DB is a pytest tmp dir, fully empty | `{alerts:0, spcx:0, id8800:0, lobbying:0, members:0, cluster:0}` |
| V7 `_seed_cluster()` creates what it asserts on | 0→1 cluster alert, 0→3 members, no SPCX, no id 8800 |
| V8 `_seed_lobbying()` creates what it asserts on | 0→2 filings |
| V9 the four fixed tests, selected individually | 4 passed |
| V10 diff touches no forbidden path | none |

**Nothing overturned.** The headline stands: the suite is genuinely green without
the knob, and no production data leaked back in.

## 6. Merge readiness

**Nothing is blocking WS1.** Both original blockers are closed — C4 by the four
tests now self-seeding, C5 by the human's Railway confirmation. The branch carries
9 commits and is unpushed.

## 7. Closing status table

| Item | Status |
| ---- | ------ |
| `SCOPE_TEST_SEED_DB` removed; 134/134 on empty default | PROVEN — knob absent from code; setting the var anyway is inert (134/134, was 131/134) |
| C5 recorded RESOLVED (human-confirmed provenance) | PROVEN (caveat) — the *record* is written and correct; the underlying Railway fact is human-reported and **not independently verifiable from here** |
| `.claude/agents/*.md` tracked; no secrets; settings still ignored | PROVEN — `git add -n` stages exactly 6 files; secret/path/email scans clean; simulated local settings still ignored |
| C4 completion written to session note | PROVEN — this note; pointer added to `00_Index.md` |
| No rule/scoring/conftest logic touched | PROVEN — `git diff HEAD --name-only` matches no forbidden path; conftest diff is SEED_DB removal only, `tmp_path`/`monkeypatch`/`yield` intact |
| Verifier independent re-run | PROVEN (caveat) — 10 checks passed, nothing overturned; run as an **explicit manual pass**, not by the `verifier` subagent, which cannot load until a restart |
| WS1 ready to merge | PROVEN — no open blockers; human merge still required, branch unpushed |

## Findings — FLAGGED FOR LATER

- **F1.** `.claude/settings.json` (not just `settings.local.json`) is currently
  ignored by the new pattern. If team-wide hook config should be shared, that
  needs its own negation line — deliberately not added, since no such file exists
  and the brief asked only for the agents.
- **F2.** The `verifier` subagent still has never actually run. Its first real
  exercise will be WS2.
- **F3.** Historical `SCOPE_TEST_SEED_DB` references remain in
  [[SESSION-2026-07-26-test-isolation]] on purpose — they record what was true
  that day. Both are annotated as resolved so a reader cannot mistake them for
  current behaviour.

## Provenance

All measurements from this machine, branch `fix/test-isolation-and-untrack-db`.
The working `Scope/data/jpt.db` was never written to — every suite run was
redirected by `tests/conftest.py`, with checksums recorded before and after.
Production was not reached; the C5 fact is human-reported.

## Human-gated

Not merged, not pushed. Per `Scope/CLAUDE.md` the merge is a conscious human act.
Nothing in this session touched rule, scoring, or corroboration logic, and no
migration was run.

## Next

WS2 — RULE_06 reliability, the insider instrument the threshold-3 gate needs as a
third leg. Restart the session first so `verifier` loads.

---

### Related

[[SESSION-2026-07-26-test-isolation]], [[2026-07-25-gate-redesign]],
[[2026-07-25-subagent-roster]], [[Current Blockers]]
