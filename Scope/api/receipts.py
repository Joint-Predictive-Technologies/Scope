"""
Alert provenance — "show the receipts".

Assembles, SERVER-SIDE and from FACTUAL DB data only (never LLM-generated), a
normalized receipts object for an alert: the named actors, source documents,
and transaction/action details that back the alert. The client renders this
structure into a compact-by-default, expandable block.

Design notes
------------
- Rules store provenance in heterogeneous shapes (JSON `detail`, JSON `tags`,
  CSV `tags`, `source_url`/`verify_url`, `member_id`, `theme_id`). This module
  normalizes whatever IS present and degrades gracefully; it never fabricates a
  field or a URL.
- When a whole class of receipt is missing for a rule (e.g. RULE_06 stores no
  SEC Form 4 URL at ingestion time), `gap` names the missing piece so the UI can
  say so honestly rather than paper over it. These are tracked as follow-ups.

Return shape (or None if there is genuinely nothing to show):
    {
      "kind":    str,               # cluster|corroboration|member_trade|insider|...
      "summary": str,               # compact one-liner shown inline
      "items":   [ {                # expandable rows (may be empty)
          "primary": str,
          "secondary": str | None,
          "url": str | None,
          "url_label": str | None,
      }, ... ],
      "source":  {"label": str, "url": str} | None,   # primary source document
      "gap":     str | None,        # honest note when provenance is incomplete
    }
"""
from __future__ import annotations

import json
from typing import Any, Optional


# --- small safe helpers -----------------------------------------------------

def _loads(blob: Any) -> Any:
    """json.loads that never raises; returns None on failure/empty."""
    if not blob:
        return None
    if isinstance(blob, (dict, list)):
        return blob
    try:
        return json.loads(blob)
    except (ValueError, TypeError):
        return None


def _clean(s: Any) -> str:
    return str(s).strip() if s is not None else ""


def _item(primary: str, secondary: str = None, url: str = None,
          url_label: str = None) -> dict:
    return {"primary": primary, "secondary": secondary,
            "url": url, "url_label": url_label}


# --- per-rule builders ------------------------------------------------------

def _cluster(a: dict) -> Optional[dict]:
    """RULE_CLUSTER — richest receipt: each member with their PTR PDF."""
    d = _loads(a.get("detail")) or {}
    members = d.get("members") or []
    if not members:
        return None
    direction = (d.get("direction") or "").replace("consensus_", "").replace("_", " ")
    verb = {"buy": "bought", "sell": "sold"}.get(direction, direction or "traded")
    items = []
    all_dates = []
    for m in members:
        dates = m.get("transaction_dates") or []
        sizes = m.get("sizes") or []
        all_dates += dates
        dir_m = (m.get("direction") or "").upper()
        sec = " · ".join(filter(None, [
            dir_m or None,
            "; ".join(sizes) if sizes else None,
            ", ".join(dates) if dates else None,
        ]))
        items.append(_item(
            _clean(m.get("name")) or _clean(m.get("member_id")) or "Member",
            secondary=sec or None,
            url=m.get("filing_url") or None,
            url_label="PTR PDF" if m.get("filing_url") else None,
        ))
    all_dates = sorted(d for d in all_dates if d)
    span = (all_dates[0] if all_dates else "")
    if all_dates and all_dates[-1] != all_dates[0]:
        span = f"{all_dates[0]} → {all_dates[-1]}"
    summary = f"{len(members)} members {verb} {a.get('ticker','')}".strip()
    if span:
        summary += f", {span}"
    src = a.get("verify_url") or a.get("source_url")
    return {
        "kind": "cluster", "summary": summary, "items": items,
        "source": {"label": "House PTR filings", "url": src} if src else None,
        "gap": None,
    }


def _corroboration(a: dict, conn) -> Optional[dict]:
    """RULE_10 — the contributing signals, linked via theme_signals."""
    tags = _loads(a.get("tags")) or {}
    rules = tags.get("rules") or []
    items = []
    theme_id = a.get("theme_id")
    if conn is not None and theme_id is not None:
        try:
            rows = conn.execute(
                """SELECT a.rule, a.headline, a.created_at, a.ticker
                     FROM theme_signals ts JOIN alerts a ON a.id = ts.alert_id
                    WHERE ts.theme_id = ? AND a.rule != 'RULE_10'
                    ORDER BY a.created_at DESC""",
                (theme_id,),
            ).fetchall()
            for r in rows:
                r = dict(r)
                items.append(_item(
                    r.get("rule", ""),
                    secondary=_clean(r.get("headline"))[:140] or None,
                    url=f"/ticker/{r.get('ticker')}" if r.get("ticker") else None,
                    url_label="view" if r.get("ticker") else None,
                ))
        except Exception:
            items = []
    if not items and rules:
        # No theme link available — still name the contributing families.
        items = [_item(rule) for rule in rules]
    if not items:
        return None
    # INSTRUMENTS, not rule names. "independent signals" is precisely the claim the
    # gate's instrument count answers: several rules can read ONE source, so the
    # congressional trio is one independent signal, not three. `rule_count` is kept in
    # tags for provenance only — reading it here restated the pre-D2 inflation in the
    # receipt's own words. Pre-D2 alerts have no instrument_count; they fall back to the
    # distinct families below rather than to the rule-name count.
    n = tags.get("instrument_count") or len({i["primary"] for i in items})
    return {
        "kind": "corroboration",
        "summary": f"{n} independent signals converged on {a.get('ticker','')}",
        "items": items,
        "source": ({"label": "Market Thesis war room",
                    "url": f"/thesis?id={theme_id}"} if theme_id else None),
        "gap": None,
    }


def _member_trade(a: dict) -> Optional[dict]:
    """RULE_01B — a single member's first-touch trade. PTR link NOT stored."""
    name = _clean(a.get("full_name"))
    action = size = tx_date = ""
    tags = a.get("tags")
    if tags and "|" in str(tags):
        parts = str(tags).split("|")
        # name(may contain comma) | action | size | date | ...
        if not name:
            name = _clean(parts[0])
        if len(parts) > 1:
            action = _clean(parts[1])
        if len(parts) > 2 and parts[2] not in ("", "?"):
            size = _clean(parts[2])
        if len(parts) > 3:
            tx_date = _clean(parts[3])
    if not name:
        return None
    sec = " · ".join(filter(None, [action.upper() or None, size or None,
                                   tx_date or None]))
    summary = f"{name} {action or 'traded'} {a.get('ticker','')}".strip()
    if tx_date:
        summary += f", {tx_date}"
    return {
        "kind": "member_trade", "summary": summary,
        "items": [_item(name, secondary=sec or None,
                        url=f"/member/{a['member_id']}" if a.get("member_id") else None,
                        url_label="member" if a.get("member_id") else None)],
        "source": None,
        "gap": "PTR filing link not captured for this rule "
               "(available on congressional cluster alerts).",
    }


def _insider(a: dict) -> Optional[dict]:
    """RULE_06 — SEC Form 4 insider. Name+action+multiple in tags only."""
    tags = _clean(a.get("tags"))
    name = action = multiple = ""
    if tags:
        parts = tags.split(",")
        name = _clean(parts[0])
        if len(parts) > 1:
            action = _clean(parts[1])
        if len(parts) > 2:
            multiple = _clean(parts[2])
    if not name:
        return None
    sec = " · ".join(filter(None, [action.upper() or None,
                                   f"{multiple} vs historical avg" if multiple else None]))
    return {
        "kind": "insider", "summary": f"{name} — {a.get('ticker','')} insider",
        "items": [_item(name, secondary=sec or None)],
        "source": None,
        "gap": "SEC Form 4 URL and transaction detail (shares/price/date) "
               "not captured at ingestion time.",
    }


def _contract(a: dict) -> Optional[dict]:
    """RULE_11 — federal contract award. USASpending link NOT stored."""
    parts = str(a.get("tags") or "").split("|")
    contractor = _clean(parts[0]) if parts else ""
    award_date = _clean(parts[1]) if len(parts) > 1 else _clean(a.get("event_date"))
    amount = _clean(parts[4]) if len(parts) > 4 else ""
    desc = _clean(a.get("detail"))
    if not contractor and not desc:
        return None
    items = []
    if contractor:
        items.append(_item(contractor, secondary=" · ".join(
            filter(None, [f"${amount}" if amount else None, award_date or None])) or None))
    if desc:
        items.append(_item("Award", secondary=desc[:160]))
    return {
        "kind": "contract",
        "summary": f"{contractor or 'Contract'} · {a.get('ticker','')}".strip(" ·"),
        "items": items, "source": None,
        "gap": "USASpending award link not captured at ingestion time.",
    }


def _url_receipt(a: dict, kind: str, label: str, gap: str = None) -> Optional[dict]:
    """Rules whose primary receipt is a stored source_url (OSINT/FARA/FEC/...)."""
    url = a.get("source_url")
    detail = _clean(a.get("detail"))
    tags = _loads(a.get("tags")) or {}
    # RULE_REDDIT stores the post link in tags.url, not source_url.
    if not url and isinstance(tags, dict):
        url = tags.get("url") or tags.get("source_url")
    if not url and not detail:
        return None
    items = [_item(detail[:200] or label, secondary=None,
                   url=url, url_label="open source" if url else None)] if detail or url else []
    return {
        "kind": kind, "summary": detail[:120] or label, "items": items,
        "source": {"label": label, "url": url} if url else None,
        "gap": None if url else (gap or "No source document URL captured."),
    }


def _market(a: dict) -> Optional[dict]:
    """RULE_07 — Polymarket. URL derivable from the stored slug."""
    tags = _loads(a.get("tags")) or {}
    slug = tags.get("slug") if isinstance(tags, dict) else None
    url = f"https://polymarket.com/event/{slug}" if slug else None
    return {
        "kind": "market", "summary": _clean(a.get("headline"))[:120],
        "items": [_item(_clean(a.get("headline"))[:200], url=url,
                        url_label="Polymarket" if url else None)],
        "source": {"label": "Polymarket", "url": url} if url else None,
        "gap": None if url else "Polymarket market link not captured.",
    }


def _generic(a: dict) -> Optional[dict]:
    """Fallback: show detail + any stored source/verify URL, note the gap."""
    detail = _clean(a.get("detail"))
    url = a.get("source_url") or a.get("verify_url")
    tags = a.get("tags")
    body = detail
    if not body and tags and "|" not in str(tags):
        body = _clean(tags)
    if not body and not url:
        return None
    items = [_item(body[:220], url=url, url_label="source" if url else None)] if body or url else []
    return {
        "kind": "generic", "summary": body[:120] or "Details",
        "items": items,
        "source": {"label": "Source", "url": url} if url else None,
        "gap": None if url else "No source document URL captured for this rule.",
    }


# --- dispatch ---------------------------------------------------------------

def build_receipts(alert: dict, conn=None) -> Optional[dict]:
    """Return the normalized receipts object for an alert, or None."""
    if not alert:
        return None
    rule = alert.get("rule") or ""
    try:
        if rule == "RULE_CLUSTER":
            return _cluster(alert)
        if rule == "RULE_10":
            return _corroboration(alert, conn)
        if rule == "RULE_01B":
            return _member_trade(alert)
        if rule == "RULE_06":
            return _insider(alert)
        if rule == "RULE_11":
            return _contract(alert)
        if rule == "RULE_07":
            return _market(alert)
        if rule == "RULE_OSINT":
            return _url_receipt(alert, "osint", "News source")
        if rule == "RULE_REDDIT":
            return _url_receipt(alert, "reddit", "Reddit thread")
        if rule == "RULE_12":
            return _url_receipt(alert, "filing", "FARA filing")
        if rule == "RULE_13":
            return _url_receipt(alert, "filing", "FEC record")
        return _generic(alert)
    except Exception:
        # Never let a receipt error break an alert response.
        return _generic(alert)
