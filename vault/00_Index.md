---
type: index
stage: iPhone-1
status: active
tags: [core, knowledge-base]
---

# Scope — Vault Index

Central knowledge base for the Scope project. Obsidian vault integrated with
the repo at `./vault/`. All decisions, architecture notes, session summaries,
and roadmap tracking live here.

**Start here:** [[Roadmap Tracking]] or [[Current Blockers]]

## Quick Links

- **Architecture**: [[RULE Design Decisions]] | [[Scoring System]] | [[Data Hierarchy]]
- **Current Work**: [[Known Issues and Blockers]] | [[In-Flight Sessions]]
- **Roadmap**: [[Roadmap Tracking]] | [[iPhone Stage Progress]]
- **Data**: [[Data Moat Strategy]] | [[Outcome Tracking Status]]

## What Lives in This Vault

| Directory | Purpose |
|-----------|---------|
| 01_Architecture | Rule designs, scoring decisions, system architecture docs |
| 02_Sessions | Session summaries, what was built, what's pending |
| 03_Roadmap | iPhone-stage tracking, next priorities, feature roadmap |
| 04_Known_Issues | Current blockers, technical debt, open questions |
| 05_Decisions | Architecture decisions with rationale and date |
| 06_Data_Moat | Outcome tracking, labeled data strategy, calibration progress |

## Metadata Convention

Every note uses YAML frontmatter:
```
---
type: decision | session-summary | architecture | roadmap | issue
stage: iPhone-1 | iPhone-5 | iPhone-8 | iPhone-12 | iPhone-15
status: pending | in-progress | implemented | deployed | blocked
priority: critical | high | medium | low
tags: [tag1, tag2]
related: [[Note Name]], [[Another Note]]
---
```

This makes notes queryable: "show me all critical stage-iPhone-5 items" or
"what decisions did we make about RULE_CLUSTER?"

## Session Workflow

Before a session:
- Claude Code views this vault to understand context
- Checks [[Current Blockers]] and [[In-Flight Sessions]]
- Reads related [[RULE Design Decisions]] or [[Roadmap Tracking]]

During a session:
- Claude references vault context when making decisions
- Adds quick notes if design rationale changes

After a session:
- Claude writes a session summary to 02_Sessions/
- Updates [[Current Blockers]], [[In-Flight Sessions]], [[Roadmap Tracking]]
- Commits vault changes alongside code changes

## Integration with Main Docs

This vault complements (not replaces) the project's main docs:
- `CLAUDE.md` — canonical architecture and rule reference (stays in repo root)
- `SCOPE_PRODUCT_SPEC.md` — external product specification
- `SCOPE_IPHONE15_VISION.md` — long-term vision
- `vault/` — internal decision log, session history, and context
