# Scope — Marketing Drafts (internal, not for publication)

> **Status: DRAFTS for human review.** Nothing here is published. Every claim
> traces to verified product capabilities (as of 2026-07-22) — no performance
> figures, win rates, returns, or user counts. Scope is a research /
> information-aggregation tool built on public data — **not investment advice**;
> keep that framing in anything that ships.
>
> Drafted 2026-07-22 via the `marketing-drafter` workflow. Edit freely before use.

---

## 1. Positioning Statement

Scope is a political-market intelligence terminal that watches the machinery of
government and money — congressional trades, insider filings, lobbying, contracts,
patents, and open-source signals — and surfaces the moments when multiple
independent mechanisms converge on the same ticker before the market has fully
priced it in. It is a research and information-aggregation tool built on public
filings; it is not investment advice.

## 2. Elevator Pitch (~59 words)

Scope runs roughly 20 independent detectors across congressional trades, SEC
insider filings, lobbying, contracts, campaign finance, patents, FARA
registrations, and open-source intelligence — then flags when four or more of them
independently converge on the same ticker within 24 hours. Two honest, never-merged
scores — Evidence Confidence and Opportunity — tell you what's supported and what's
left. Research only, not investment advice.

## 3. Tagline Options

1. "You keep the judgment. Scope keeps the watch." *(existing)*
2. "One signal is noise. Four converging is a thesis."
3. "Public data. Independent detection. Honest scoring."

## 4. Proof-Points List

- **~20 independent detection rules** spanning congressional PTRs (House & Senate),
  SEC Form 4 insider trades, Senate LDA lobbying disclosures, USASpending federal
  contracts, FEC campaign finance, PatentsView patents, FARA foreign-agent filings,
  Federal Register rules, Polymarket prediction markets, GDELT geopolitical OSINT,
  Reddit, Telegram, ADS-B flight tracking (OpenSky), statistical anomaly detection,
  and options-flow correlation.
- **Corroboration engine:** fires an alert and builds a "Market Thesis" only when
  4+ distinct detection mechanisms independently converge on the same ticker within
  24 hours.
- **Congressional cluster detection:** flags when 3+ members trade the same ticker
  within a 72-hour proximity window, accounting for the 30–45 day disclosure lag.
- **Two independent scores, never merged:** Evidence Confidence (how well-supported)
  and Opportunity (how much edge may remain after the market absorbs a signal), plus
  a separate novelty score that decays as a pattern recurs. All are fixed at
  detection time and never retroactively rewritten.
- **Daily Morning Brief:** 7 sections summarizing activity across every tracked
  mechanism from the prior 24 hours.
- **Outcome tracking:** every alert is followed forward at +1/+5/+20 trading days,
  SPY-relative, into a growing, proprietary outcome dataset — a record of what
  actually happened after Scope spoke. *(Still maturing; no performance figures
  published yet.)*
- **Fully explainable:** every alert decomposes to its underlying evidence; the LLM
  writes context, never predictions or recommendations.
- **Live in production** on a scheduler running ~32 jobs.

## 5. Launch Social Posts

**X — Post 1**
One insider filing is a data point. One congressional trade is a headline. Four
independent mechanisms converging on the same ticker within 24 hours is something
else. That's what Scope watches for. Research tool, not investment advice.

**X — Post 2**
Congress has a 30–45 day disclosure lag. Scope accounts for it — and flags when 3+
members trade the same name inside a 72-hour window once the filings land. Public
data, watched continuously. Not investment advice.

**LinkedIn — Post 1**
Most "alt-data" tools scrape one source and call it an edge. Scope runs roughly 20
independent detection mechanisms — congressional trades, SEC Form 4 filings,
lobbying disclosures, federal contracts, campaign finance, patents, foreign-agent
registrations, prediction markets, and open-source signals among them — and looks
for the moments when several of them independently point at the same ticker. When
four or more converge within 24 hours, Scope builds a "Market Thesis" laying out
exactly what evidence is behind it. It doesn't tell you what to do with that — it
surfaces and contextualizes, and gets out of the way. Scope is a research and
information-aggregation tool built on public filings and open sources. It is not
investment advice.

**LinkedIn — Post 2**
Every signal Scope surfaces gets two honest, separate scores: Evidence Confidence
(how well-supported is this, structurally) and Opportunity (how much room may
plausibly be left after the market has had a chance to react). We don't merge them
into one number, because "well-supported" and "still has edge" are different
questions, and collapsing them would hide that. Both are fixed the moment an alert
fires and never rewritten after the fact. That's what an explainable system looks
like — you can trace every alert back to the filings and events underneath it.
Scope is not investment advice; it's a way to not miss the signal while keeping
your own judgment intact.

**X — Post 3**
Scope's Daily Morning Brief: 7 sections, last 24 hours, across congressional trades,
insider filings, lobbying, contracts, patents, and open-source signals. You keep the
judgment. Scope keeps the watch. Not investment advice.

## 6. Landing-Page Copy

**Hero headline:**
The machinery of government and money, watched continuously.

**Subhead:**
Scope surfaces structurally-meaningful convergences across roughly 20 independent
public-data detection mechanisms — before the market has fully priced them in. A
research and information-aggregation tool. Not investment advice.

**Feature blurb 1 — Corroboration**
*Convergence, not coincidence.*
When four or more independent detection mechanisms — congressional trades, insider
filings, lobbying, contracts, patents, open-source intelligence, and more — point at
the same ticker within 24 hours, Scope fires a corroboration alert and builds a
Market Thesis showing exactly which evidence lines up. One signal is a data point.
Four converging independently is structurally different.

**Feature blurb 2 — Dual scoring**
*Two honest scores, never merged.*
Evidence Confidence measures how well-supported a signal is. Opportunity measures
how much room may be left once the market has had time to react. A separate novelty
score decays each time a pattern recurs. All are fixed the moment an alert fires and
never retroactively rewritten — so you're always seeing what Scope actually knew at
the time, not a revised story.

**Feature blurb 3 — Outcome moat**
*A record that compounds.*
Every alert Scope surfaces is tracked forward — at +1, +5, and +20 trading days,
relative to SPY — into a growing, proprietary outcome dataset. This record of what
actually happened after Scope spoke gets more valuable every day it runs, and can't
be replicated by a competitor starting from zero. This dataset is still maturing;
we're not yet publishing performance figures from it.

**Closing CTA:**
Scope doesn't tell you what to do. It tells you what's happening, and why it's worth
your attention. You keep the judgment. Scope keeps the watch.
