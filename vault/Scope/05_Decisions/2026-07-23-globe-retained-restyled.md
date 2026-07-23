---
aliases: [Globe Retained and Restyled]
type: decision
stage: iPhone-5
status: accepted
priority: medium
tags: [decision, ui, osint, globe, design]
related: [[2026-07-23 UI Restoration and Completion]], [[Roadmap Tracking]]
date-created: 2026-07-23
---

# Decision: keep the OSINT globe, restyle it as an instrument

**Branch:** `fix/ui-restoration-and-completion` (Phase 4b). File: `osint.html`
(Three.js WebGL).

## What

The globe route stays. Its rendering was reworked from a bright satellite-photo
ornament into a dark instrument:

- **Dots colored by severity token** (critical `#e06868` / high `#e88b4a` /
  medium `#c89664`) instead of the old uniform bright red — hex mirrors
  `tokens.css` because WebGL can't read CSS custom properties.
- **Dot size scales with signal count** (`1.8 + sqrt(count)*1.1`, clamped), not
  severity — busier hotspots read bigger.
- **Ocean/void = `--surface-canvas`**; the Blue Marble relief is tinted dark
  (`0x3a3a42`) so landmasses read near `--surface-2` — geography stays legible
  but muted.
- **No glow/bloom**: atmosphere sphere removed; the per-dot ring is now **static**
  (the old continuous pulse was decorative animation, which the design system
  forbids).
- Legend + severity badges retokenized (dropped hardcoded `#ff2020`/`#e55b4d`/etc.).

## Why

The red dots read as noise/decoration, not signal. Severity color + count size +
a dark palette make the globe an at-a-glance instrument. Keeping it (vs. cutting)
was worth it because geospatial clustering of OSINT signals is genuinely useful
context the flat feed can't show.

## Deferred (follow-up, not blocking)

- Lat/lon **graticule** hairlines.
- **On-hover** dot-expand + tooltip (location + count + top ticker). The
  click-to-open side panel already covers hotspot detail, so this is polish.

Two hardcoded hex remain by necessity in the Three.js layer (material colors) —
WebGL requires numeric hex, so these mirror the token values and can't be CSS vars.

See also: [[2026-07-23 UI Restoration and Completion]].
