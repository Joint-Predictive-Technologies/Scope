# Phase Zero Spec — Political & Insider Activity Tracker

**Project working name:** Scope (placeholder)
**Status:** Draft v0.1 — pre-code, pre-implementation
**Author's role context:** Building a research-augmentation tool for active investors, not a signal/advice product

---

## 1. The one-paragraph pitch

Scope is a research terminal for macro and event-driven retail investors who already do their own thinking but cannot watch every corner of the internet at once. It watches political and insider activity — congressional trades, executive social media, hearing schedules, regulatory filings, prediction-market positioning, lobbying disclosures — and surfaces signal-bearing events with LLM-generated context, cross-referenced against the user's watchlist and stated theses. The user keeps the judgment; the tool keeps the watch. Positioning metaphor: a scope for a gun. The shooter aims; the scope sees.

---

## 2. First user persona

**Name (archetype):** "Marcus" — the macro/event-driven prosumer.

- Mid-30s to mid-50s. Trades his own account, somewhere between €25k and €500k portfolio.
- Follows roughly 50–200 accounts on X actively. Reads a few Substacks. Has Polymarket and Kalshi accounts. Has a brokerage with options enabled.
- Identifies as "not a day trader" — typical holding periods are days to months. Allocates around themes: rate cuts, election outcomes, geopolitical shocks, regulatory shifts, sector rotations triggered by policy.
- Already pays for at least one of: a financial newsletter (€20–50/mo), Benzinga Pro / Koyfin / TradingView Premium, a Discord community.
- Pain point: information overload. Knows the signals exist but cannot keep up. Misses the Pelosi filing by three days. Sees the Trump Truth Social post about chips two hours after the move. Reads the policy thread the morning after the futures already gapped.
- Willingness to pay: €30–100/month for something that *demonstrably* surfaces things he would otherwise miss.

**Why this persona over the others:** Higher willingness to pay than pure crypto narrative traders, less crowded than the options-flow audience, and the product narrative ("political signals → markets") aligns directly with the data we're best-positioned to collect. Polymarket traders are a closely adjacent secondary audience and will likely convert for free as a side effect of the same data.

---

## 3. Vertical scope (what we cover at launch)

Three signal domains, in priority order:

**Domain A — Congressional & insider activity**
- US House STOCK Act filings (Periodic Transaction Reports)
- US Senate financial disclosures
- SEC Form 4 filings (corporate insider transactions)
- 13F / 13D / 13G institutional filings (slower, but cross-referenced)

**Domain B — Political signal surface**
- Truth Social posts by Trump and a curated list of administration figures
- X posts by a curated list of US senators, representatives, and policy-relevant accounts (Warren, Cruz, Hawley, Cotton, Khanna — accounts that move tickers when they post)
- White House press briefing schedule and transcripts
- Congressional hearing calendar (Senate Banking, House Financial Services, Senate Intelligence)
- Federal Register publications (rule proposals, executive actions)

**Domain C — Adjacent prediction-market context**
- Polymarket position changes on politically-linked markets
- Kalshi event prices (where API access permits)
- Large position movements flagged separately from price movements

**Explicitly out of scope at launch:**
- Equity fundamentals, earnings, technicals (Koyfin/TradingView do this)
- Options flow (Unusual Whales lane)
- Crypto on-chain (separate product later)
- Sports, weather, non-US politics (geographic expansion later)
- Anything requiring real-time market data feeds we'd have to pay six figures for

---

## 4. Latency tiers — the hybrid model

**Tier 1: Fast path (target 30 sec – 5 min from source publication)**

For events where speed materially affects user value. Minimal processing, immediate push notification, structured payload.

Examples:
- STOCK Act filing hits the House clerk site → ticker, member name, transaction type, amount band, link, push within minutes
- Trump Truth Social post containing a watched ticker symbol or company name → text, sentiment label, cross-reference to user watchlist
- SEC Form 4 from a tracked executive → standard filing summary

**Tier 2: Slow path (target 5 – 30 min, sometimes longer)**

For events that benefit from analysis, context, and cross-referencing. LLM-generated summary, related events, historical precedent if applicable.

Examples:
- A Senate hearing transcript → "what was said about Company X, here's the relevant section, here's how the stock moved during the hearing"
- A multi-tweet policy thread → consolidated summary, identified affected sectors, related prior posts by the same author
- A Federal Register rule proposal → "this affects these tickers because, here's the comment period, here's the historical pattern when similar rules were proposed"

**Why hybrid is correct:** A pure-speed product loses to professional terminals on infrastructure. A pure-context product loses to the user's own ability to read carefully when they have time. The hybrid says: be fast where speed is the whole point, be smart where context is the whole point. The LLM era makes the slow path genuinely defensible in a way it wasn't five years ago.

---

## 5. The first ten launch rules

**Rule 1 — STOCK Act new filing**
Trigger: New PTR appears on house.gov or senate.gov disclosure portals.
Surface: Member, ticker, buy/sell, amount band, filing date vs transaction date (delay flag), link.
Tier: Fast.
Cross-reference: User's watchlist; member's prior trades in same ticker.

**Rule 2 — Cluster trade detection**
Trigger: Three or more congressional members file transactions in the same ticker within a 7-day window.
Surface: Ticker, member list, aggregate direction, time clustering chart.
Tier: Slow (requires aggregation).
Cross-reference: News events in that ticker during the window.

**Rule 3 — Trump Truth Social ticker mention**
Trigger: New Trump post mentions a company name, ticker, or sector keyword from a maintained dictionary.
Surface: Post text, identified entities, sentiment, premarket/intraday price reaction.
Tier: Fast.
Cross-reference: Prior posts mentioning the same entity; price reaction history.

**Rule 4 — Senate hearing scheduled with public-company witness**
Trigger: Senate or House committee schedules a hearing with executives of a public company as witnesses.
Surface: Company, hearing topic, committee, date, witness list, prior hearings for that company.
Tier: Slow (lead time is days; analysis matters more than speed).
Cross-reference: Stock's reaction to prior hearings; options IV change since announcement.

**Rule 5 — Watched senator/rep posts on a policy area**
Trigger: One of ~40 tracked accounts posts content matching a sector-policy keyword set (Warren on banks, Cruz on energy, Hawley on tech antitrust).
Surface: Post, identified sector, prior pattern of this author influencing the sector.
Tier: Fast for the post itself; slow for the contextualized version 10 min later.
Cross-reference: Author's historical ticker mentions; sector ETF intraday move.

**Rule 6 — Executive insider trade (Form 4) — significance flag**
Trigger: Form 4 filing where transaction value exceeds a threshold *and* deviates from the executive's historical pattern.
Surface: Executive, company, transaction, deviation flag explanation.
Tier: Slow (pattern detection requires history lookup).
Cross-reference: Recent material company news; upcoming earnings date.

**Rule 7 — Polymarket significant position movement on politically-linked market**
Trigger: Volume or price move >X% on a Polymarket market in the politics / policy / geopolitics category.
Surface: Market question, price move, volume, related public-equity tickers (if mappable).
Tier: Fast.
Cross-reference: News during the movement window.

**Rule 8 — Federal Register sector-impact rule**
Trigger: New rule proposal published in the Federal Register affecting a tracked sector keyword set.
Surface: Rule summary (LLM-generated), affected sectors, comment period dates, historical pattern of similar proposals.
Tier: Slow.
Cross-reference: ETF and key ticker reactions; prior similar rules and their outcomes.

**Rule 9 — Lobbying disclosure spike**
Trigger: A company's quarterly lobbying spend (LD-2 filing) increases >50% year-over-year.
Surface: Company, spend delta, issues lobbied, recent regulatory context.
Tier: Slow.
Cross-reference: Pending legislation touching the issues.

**Rule 10 — Cross-source corroboration alert (the marquee feature)**
Trigger: Two or more rules above fire on the same ticker within a 48-hour window.
Surface: Combined timeline of events, narrative summary, "why this is interesting" LLM-generated paragraph.
Tier: Slow.
Cross-reference: This is the cross-reference. It's the rule that makes the product feel like more than the sum of its data sources.

**Note on Rule 10:** This is the rule that justifies the product existing. Any individual rule above can be replicated by a competitor. The value is in the corroboration layer that knits them together. The user-customizable version of Rule 10 is what the eventual moat looks like.

---

## 6. What the user actually sees

A single feed, reverse chronological, with:
- Per-card: rule name (the rule that fired), entities (tickers, people), one-line summary, expand for detail
- Filters: by domain (insider / political / prediction-market), by user watchlist, by rule type
- Push notifications: opt-in per rule type, with daily/hourly digest options for slow-path items
- A "thesis" field where the user types what they're watching for in natural language ("rate cuts in Q3, semiconductor export controls, Brazil political risk") and the relevance engine filters accordingly

The thesis-driven filtering is the under-the-hood LLM differentiation. Older tools do keyword matching. Scope does semantic matching against a user's stated worldview. This is genuinely new in this category.

---

## 7. What we are not doing at launch

- No buy/sell recommendations of any kind, ever. The product surfaces events. The user decides.
- No backtested "if you'd followed Pelosi you'd be up 40%" marketing. That positions us as a signal product, which is the wrong category.
- No portfolio tracking or brokerage integration. Out of scope, regulated, slow to build.
- No "AI predictions." The LLM is used for summarization, entity extraction, semantic filtering, and contextualization — not prediction.
- No paywalled signals in the marketing copy. Free tier shows the firehose; paid tier shows the personalized, contextualized, cross-referenced version.

---

## 8. Regulatory posture

Position: Scope is an information aggregation and research tool. It is not investment advice. It does not provide personalized recommendations. It does not manage funds. Standard disclaimers throughout the product. Terms of service explicit on this.

This positioning maps closely to how Benzinga, Capitol Trades, and Quiver operate. The line to not cross: do not generate text that reads as "you should buy X" or "this is a good trade." The product describes, contextualizes, and surfaces. It does not advise.

Before any paid launch: a lawyer familiar with EU financial services regulation reviews the terms, the product copy, and the LLM-generated output templates.

---

## 9. Distribution

- Public X account that posts interesting findings the tool catches, manually curated. Build the audience that becomes the launch channel. Aim for 1k followers before launch.
- Build-in-public posts: monthly progress, technical writeups, screenshots of the product as it develops. Capitol Trades and Unusual Whales both grew this way.
- Identify the 10–20 X accounts that Marcus (the persona) follows. Engage genuinely. Don't pitch.

---

## 10. Open questions

1. **Build vs. buy on data ingestion.** Some sources have clean APIs (Polymarket, SEC EDGAR). Others require scraping (House/Senate disclosure portals, Truth Social, X). X API access is expensive and restrictive. Decide per source.
2. **Tech stack baseline.** Python + Postgres + Redis is the obvious default. LLM layer: Anthropic API (Claude) for the summarization/extraction layer.
3. **Pricing model at launch.** Free tier scope, paid tier price point, annual vs monthly.
4. **Geographic targeting.** Product is US-political-data-heavy. User base could be global.
5. **Naming and brand.** "Scope" is a placeholder. The metaphor is good; the name might be taken.
6. **Solo vs. team.** Realistic to build this solo? Or is a technical co-founder / first hire on the roadmap?

---

*End of Phase Zero Spec v0.1.*
