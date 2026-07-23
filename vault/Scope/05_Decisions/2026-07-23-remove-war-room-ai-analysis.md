---
aliases: [Remove War-Room AI Analysis]
type: decision
stage: iPhone-5
status: accepted
priority: medium
tags: [decision, ui, llm, war-room]
related: [[2026-07-23 UI Restoration and Completion]], [[Roadmap Tracking]]
date-created: 2026-07-23
---

# Decision: remove the "AI Analysis" block from alert war rooms

**Branch:** `fix/ui-restoration-and-completion` (Phase 4a).

## What

The stored `alert.detail` LLM narrative — surfaced as **"AI Analysis"** on ticker
war rooms (`ticker.html`) and **"Analysis"** in the feed (`alerts.html`) — is no
longer displayed. Factual receipts, tags, and the other detail blocks (Historical
Context, Thesis Relevance, Members) carry the war room instead.

## Why

- It wasn't earning its space — generic LLM prose next to hard receipts dilutes
  rather than adds. **Factual provenance builds more trust with users than AI
  narrative**, which is Scope's whole credibility posture.
- The dedicated war-room pages (`cluster.html`, `thesis.html`) never had an LLM
  block to begin with (receipts + user notes only), so this only touched the two
  places the narrative actually rendered.

## Cost / reversibility

- **No per-load Groq call existed** — `alert.detail` is written at ingestion and
  stored on the alert, so removing the UI display stops nothing at runtime and
  saves no tokens. It is purely a display decision.
- Generation code is **left intact and unwired** — the call sites are commented
  with a pointer to this note (`grep "remove-war-room-ai-analysis"`). Re-enabling
  is a one-line uncomment if we ever want it back.

See also: [[2026-07-23 UI Restoration and Completion]].
