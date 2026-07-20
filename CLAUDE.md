# Scope

## Subagents

Four scoped subagents live in `.claude/agents/`. Each is deliberately narrow;
respect the tool scope and boundaries baked into each file.

| Agent | Purpose | Tool scope |
|-------|---------|------------|
| `bug-hunter` | Diagnoses bugs and silent failures across Scope — reports root causes, never fixes. | Read, Grep, Glob, Bash (read-only discipline; no DB writes) |
| `ui-designer` | Audits and fixes visual/UX issues; verifies via browser automation. | Read, Write, Edit, Bash, Grep, Glob + Playwright MCP browser tools (templates/CSS/static/frontend JS only) |
| `troubleshooter` | Infra + production health (disk, scheduler, ingestion, Railway). Detects and reports; no prod changes without sign-off. | Read, Grep, Glob, Bash (read-only; no SSH writes/deploys) |
| `marketing-drafter` | Drafts brief/social/performance copy to a local drafts file. Never publishes. | Read, Grep (drafts only) |

### Deliberate exclusion: no scoring/dev subagent

There is intentionally **no fifth "dev" or "scoring" subagent**, and one must
not be created. Anything that touches scoring (`insert_alert`, `enrich_scores`,
novelty/opportunity calculations), corroboration logic, rule scripts
(`rule_*.py`), ingestion, or database/schema migrations stays a **manual,
human-gated session** — not autonomous agent work. These systems are where
signals can be silently lost or miscounted (DATA-LOSS class), so they require a
human in the loop by design. Do not add such an agent even when it seems
convenient.
