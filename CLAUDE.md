# Scope

## Subagents

Six scoped subagents live in `.claude/agents/`. Each is deliberately narrow;
respect the tool scope and boundaries baked into each file.

**Every one of them is read-only.** None has `Write`, `Edit`, or `NotebookEdit`.
They investigate, verify, and flag — the human (or the main session, under human
direction) makes the change.

| Agent | Purpose | Tool scope |
|-------|---------|------------|
| `data-integrity` | DB forensics and data-loss sentinel: migrations, missing/deleted rows, schema and column health, ticker/key correctness. Distinguishes generated-then-destroyed from never-generated. | Read, Grep, Glob, Bash (opus) |
| `signal-scoring` | Guards the convergence moat and dual-axis scoring: RULE_10/theme generation, corroboration matching, novelty/absorption/evidence terms, surfacing order, calibration hygiene. | Read, Grep, Glob, Bash (opus) |
| `scheduler-reliability` | Keeps every scheduled rule actually running and logging: argparse/CLI contract mismatches, import-time failures, silent exit-2, the scheduler safety net. | Read, Grep, Glob, Bash (opus) |
| `verifier` | Independent final pass on any work order producing PROVEN/UNVERIFIED claims — re-derives each claim from trusted data or code and overturns the headline when warranted. | Read, Grep, Glob, Bash (opus) |
| `diff-gatekeeper` | Pre-merge reviewer. Reviews the diff against Scope's workflow rules and flags data/scoring/migration changes for extra scrutiny. Never merges. | Read, Grep, Glob, Bash (opus) |
| `provenance-guardian` | Anti-slop and honesty reviewer for anything user-facing: reports, claims, numbers, UI copy. Advisory only. | Read, Grep, Glob (sonnet) |

### Deliberate exclusion: no dev/scoring subagent

There is intentionally **no "dev" or "scoring" subagent with write access**, and
one must not be created. The roster above is diagnostic and advisory by design:
`signal-scoring` and `data-integrity` *read* those systems, they do not change
them.

Anything that touches scoring (`insert_alert`, `enrich_scores`,
novelty/opportunity calculations), corroboration logic, rule scripts
(`rule_*.py`), ingestion, or database/schema migrations stays a **manual,
human-gated session** — not autonomous agent work. These systems are where
signals can be silently lost or miscounted (DATA-LOSS class), so they require a
human in the loop by design. Do not add such an agent, and do not widen an
existing agent's tool scope to include writes, even when it seems convenient.
