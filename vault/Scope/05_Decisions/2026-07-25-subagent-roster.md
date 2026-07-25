---
type: decision
status: active
priority: high
tags: [decision, subagents, workflow, human-gated]
related: [[Current Blockers]], [[RULE Design Decisions]], [[Scoring System]]
date-created: 2026-07-25
---

# Decision: specialist subagent roster (advisory, human-gated)

**Date:** 2026-07-25
**Scope of this pass:** config files under `.claude/agents/` and this note. No
application code, no data, no migrations.

## The decision

Scope gets a roster of **five advisory specialist subagents** that the
human-driven main session delegates to. They investigate, review, and propose
diffs. They do **not** merge, deploy, mutate the database, or run migrations.

**The standing boundary is unchanged and reaffirmed:** autonomous,
production-mutating agents remain excluded from Scope by deliberate decision.
Anything touching scoring (`insert_alert`, `enrich_scores`, novelty/opportunity),
corroboration, rule scripts, ingestion, or schema migrations stays a manual,
human-gated session — these are the systems where signals are silently lost or
miscounted. That rule holds harder right now, because the convergence layer emits
nothing (see below).

## The roster

| Agent | Purpose | Model / effort | Tools |
|---|---|---|---|
| `data-integrity` | DB forensics and data-loss sentinel — migrations, missing rows, dead columns, ticker/key hygiene | opus / high | Read, Grep, Glob, Bash |
| `signal-scoring` | Guards the convergence moat and the dual-axis scoring — RULE_10/themes, novelty/absorption/evidence, surfacing order, calibration hygiene | opus / high | Read, Grep, Glob, Bash |
| `scheduler-reliability` | Keeps every scheduled rule running and logging — CLI contract mismatches, silent exit-2, import-time failures, the safety net | opus / high | Read, Grep, Glob, Bash |
| `provenance-guardian` | Anti-slop and honesty reviewer for anything user-facing — receipts, snapshot-vs-prod labeling, no premature `win_rate` | sonnet | Read, Grep, Glob |
| `diff-gatekeeper` | Pre-merge reviewer — scope creep, read-only violations, high-scrutiny changes, tests, vault hygiene | opus / high | Read, Grep, Glob, Bash |

The `tools` allowlist is the blast-radius bound. `provenance-guardian` has no
`Bash` deliberately — it reviews claims and cannot run anything.

### Deviations from the original spec, and why

1. **`scheduler-reliability` is opus/high, not sonnet.** Chosen by the human when
   asked. `effort: high` was added for consistency with the other opus agents.
2. **`sourcing-scout` was not created.** Declined by the human. New-feed
   evaluation has nothing to feed while convergence emits nothing; revisit if and
   when it fires.
3. **No agent has `Write`.** The spec's tool lists omit it, but the shared
   guardrail block told each agent to write its own vault session note — a
   contradiction. Resolved in favour of the allowlist, since the spec states the
   allowlist is the blast-radius bound. Each agent instead **returns** the session
   note body and the index pointer in its final report, and the main session
   writes both. This also keeps vault writes under human review.
4. **Vault paths corrected to the real structure.** The spec referenced
   `vault/sessions/`, `TEMPLATE.md`, `vault/Sessions.md` and
   `vault/decisions/DEC-subagent-roster.md`. None exist. The actual convention is
   `vault/Scope/02_Sessions/` with `SESSION_TEMPLATE.md`,
   `vault/Scope/05_Decisions/YYYY-MM-DD-slug.md`, and `vault/Scope/00_Index.md`
   as the index. The agents were given the real paths — the spec's paths would
   have failed on first use.
5. **Stale premises in the spec were corrected in the agent prompts.** The brief
   described an "unresolved data-loss suspicion" and a "~62% of alerts missing"
   figure. Both were settled on 2026-07-25 (see below). Encoding them as live
   would have sent `data-integrity` chasing a closed case indefinitely.

## Overlap with the documented roster — and a finding

`CLAUDE.md:5` states that four scoped subagents live in `.claude/agents/`:
`bug-hunter`, `ui-designer`, `troubleshooter`, `marketing-drafter`.

**None of them exist.** There is no `.claude/` directory anywhere in the repo, no
user-level `~/.claude/agents/`, and `git log --all -- .claude/` is empty — they
were never committed. `.claude/` is listed in `.gitignore:5`.

Consequences, all needing a human decision:

- **`CLAUDE.md` currently documents four agents that are not on disk.** Either
  they exist only on another machine, or the documentation is aspirational. It
  should be reconciled either way.
- **This new roster is machine-local too.** Because `.claude/` is gitignored, the
  five files created here are not version-controlled and will not reach anyone
  else. The human chose to leave the ignore in place for now and flag it. If the
  roster is meant to be a standing team convention, `.claude/agents/` needs a
  `.gitignore` negation — a one-line change deliberately not made in this pass.
- **`vault/Scope/02_Sessions/_session-log.md` references a SessionEnd hook in
  `.claude/settings.json`**, which also does not exist. The auto-appended session
  log is therefore not running.

### Consolidation recommendation

Since the four documented agents are absent, there is no live duplication today.
If they are restored, these overlaps need resolving rather than leaving near-twins:

- **`scheduler-reliability` vs `bug-hunter`.** `bug-hunter` is described as
  diagnosing bugs and silent failures across Scope — which fully contains
  `scheduler-reliability`'s remit. **Recommendation:** keep
  `scheduler-reliability` as the narrow specialist (CLI contracts, exit-2, the
  safety net) and either retire `bug-hunter` or narrow it to application-logic
  bugs explicitly excluding the scheduler.
- **`scheduler-reliability` vs `troubleshooter`.** `troubleshooter` owns infra and
  production health (disk, scheduler, ingestion, Railway). The scheduler appears in
  both. **Recommendation:** split on environment — `troubleshooter` owns *live
  production* health, `scheduler-reliability` owns *code-level* contract
  correctness. Or merge them; do not run both against the scheduler unscoped.
- **`diff-gatekeeper` vs `troubleshooter`.** Minimal real overlap
  (`troubleshooter` detects and reports, does not review diffs). No action needed.
- **`provenance-guardian` vs `marketing-drafter`.** Complementary, and worth
  wiring: `marketing-drafter` produces copy, `provenance-guardian` should review it
  before anything ships.
- **`data-integrity` and `signal-scoring` have no counterpart** in the documented
  four. They are the genuinely new capability, and they are also the two closest to
  the human-gated boundary — hence advisory-only, read-only DB, propose-don't-apply.

## Context the agents encode (as of 2026-07-25)

Recorded here so the roster's assumptions are auditable and can be retired when
they stop being true:

- **Convergence has never fired on real data.** 0 RULE_10 alerts, 0 themes, 0
  theme_signals; 0 ticker/24h-windows have ever met the 4-distinct-eligible-rule
  gate (best ever: `SPCX` at 3). The moat is inert, not degraded.
- **The RULE_10 "data loss" case is closed.** All 28 emits were the `ZWAR` test
  fixture; `sqlite_sequence` shows 28 themes and 140 = 28×5 theme_signals created
  and deleted by the test teardown. The "~62% of alerts missing" figure is
  explained by documented purges (commit `3dd7df5`, "2134 false alerts purged").
  Full evidence: [[SESSION-2026-07-25-rule10-convergence-trace]].
- **Both scoring axes are degenerate.** `absorption_pct` is 0.0 on all 3,347 rows;
  `evidence_confidence` has 3 distinct values encoding only the rule's name.
- **No user-facing surface ranks by `opportunity_score`.** The ones that do read
  the empty `themes` table.
- **Calibration is thin and skewed.** 324 complete outcomes, 135 of them (42%) a
  single ticker (SPY) from RULE_07, a rule excluded from corroboration. No rule has
  enough non-generic outcomes for a win rate.
- **Tests write to the live database**, and `Scope/data/jpt.db` is tracked in git.

## Activation

File-based agents load at session start. **The human must restart the Claude Code
session (or run `/agents`) before these are available.**

---

### Related

[[Current Blockers]], [[SESSION-2026-07-25-rule10-convergence-trace]],
[[RULE Design Decisions]], [[Scoring System]]
