from __future__ import annotations
"""
apps/interaction-audit/rca.py — build a Case Summary & Root Cause Analysis (Slack-mrkdwn)
from an analyzed audit dict. Deterministic v1 (structure mirrors the "32 Mayflower" gold
standard); the narrative-quality version comes when the LLM/Runlayer layer is connected.
"""
import re
from datetime import datetime


def _strip(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


def build_rca(result: dict, flip: str) -> str:
    exp = result.get("experience", {}) or {}
    ec = result.get("economics") or {}
    def _money(v):
        try:
            return "$" + format(int(v), ",")
        except (TypeError, ValueError):
            return "—"
    product = exp.get("product") or "—"
    cnml = exp.get("cnml")
    gaps = result.get("gaps", []) or []
    mis = result.get("misstatements", []) or []
    slack = result.get("slack", []) or []
    events = [e for e in (result.get("events") or []) if e.get("type") in ("call", "text", "email")]
    events_desc = sorted(events, key=lambda e: e.get("iso", ""), reverse=True)
    today = datetime.now().strftime("%b %d, %Y")

    L = []
    L.append("📋 *Case Summary & Root Cause Analysis*")
    L.append(f"*{result.get('address') or flip}*  ·  flip `{flip}`")
    L.append(f"Customer: {result.get('customer','—')} · {product}"
             + (f" · CNML {cnml}" if cnml and cnml != "—" else "")
             + f" · HSA: {exp.get('hsa','—')} · Status: *{exp.get('state','—')}*")
    L.append(f"_Prepared {today} · auto-generated draft from the Customer Interaction Audit_")
    L.append("")

    # Executive summary
    L.append("*Executive Summary*")
    L.append(_strip(result.get("summary", "")))
    L.append("")

    # Key facts
    L.append("*Key Facts*")
    L.append(f"• Product: {product}" + (f" (CNML {cnml})" if cnml and cnml != '—' else ""))
    L.append(f"• Purchase price: {_money(ec.get('purchase_price'))}")
    L.append(f"• Offer price (headline): {_money(ec.get('offer_price'))}")
    L.append(f"• Est. net proceeds: {_money(ec.get('net_price'))}")
    if ec.get("list_price") is not None:
        L.append(f"• List price: {_money(ec.get('list_price'))}")
    L.append(f"• Offer service fee: {exp.get('fee','—')}")
    if ec.get("ai_repairs") is not None:
        L.append(f"• AI-scoped repairs: {_money(ec.get('ai_repairs'))}")
    if ec.get("dv_repairs") is not None:
        L.append(f"• Diligence repair result: {_money(ec.get('dv_repairs'))}")
    L.append(f"• Channel: {exp.get('channel','—')}")
    L.append(f"• HSA lead: {exp.get('hsa','—')}")
    L.append(f"• Status: {exp.get('state','—')}")
    L.append("")

    # Root cause analysis
    L.append("*Root Cause Analysis*")
    if gaps:
        gsorted = sorted(gaps, key=lambda g: g.get("days", 0), reverse=True)
        L.append(f"_Primary — {gsorted[0].get('where','response gap')} "
                 f"(waiting on {gsorted[0].get('owner','—')}, {gsorted[0].get('days','?')}d)_")
        L.append(_strip(gsorted[0].get("text", "")))
        for i, g in enumerate(gsorted[1:], 1):
            L.append(f"• Contributing #{i} — *{g.get('owner','—')}* / {g.get('where','')} "
                     f"({g.get('days','?')}d): {_strip(g.get('text',''))}")
    if mis:
        for m in mis:
            L.append(f"• *Accuracy miss* — {_strip(m.get('issue',''))} → {_strip(m.get('correct',''))}")
    if not gaps and not mis:
        L.append("• No systemic response gaps or accuracy misses detected in the audited window.")
    L.append("")

    # Open questions (derived)
    L.append("*Open Questions*")
    st = (exp.get("state") or "").lower()
    if any("title" in (g.get("owner", "") + g.get("where", "")).lower() for g in gaps) or "released" in st:
        L.append("• Title clear-to-close? — ⏳ verify (post-contract/title activity present)")
    if gaps:
        L.append(f"• Who owns the outstanding follow-up? — ⏳ {gaps[0].get('owner','assign')}")
    L.append(f"• Anything mis-told to the customer? — {'❌ ' + str(len(mis)) + ' flag(s)' if mis else '✅ none detected'}")
    L.append("")

    # Next steps
    L.append("*Recommended Next Steps*")
    L.append("_Immediate_")
    if gaps:
        g0 = max(gaps, key=lambda g: g.get("days", 0))
        L.append(f"• Assign an owner and respond on the {g0.get('where','open item')} ({g0.get('days','?')}d open, {g0.get('owner','—')}).")
    else:
        L.append("• No immediate response gaps — keep cadence.")
    L.append("_Systemic_")
    if any("title" in (g.get("owner", "")).lower() for g in gaps):
        L.append("• Post-contract response SLA owned by Title/TC (this deal shows a multi-day gap).")
    if mis:
        L.append("• Reinforce product-accuracy at point of quote (checked vs HSA Hub + the actual offer).")
    L.append("• Offer-history / comms tracking: keep a per-seller audit trail of what was said and shown (the core gap this tool closes).")
    L.append("")

    # Timeline (recent first)
    L.append(f"*Interaction Timeline* (most recent first · showing up to 12 of {len(events)})")
    for e in events_desc[:12]:
        body = _strip(e.get("body", "")).replace("\n", " ")
        L.append(f"• {e.get('when','')} — {e.get('who','')} ({e.get('type')}{(' '+e['dir']) if e.get('dir') else ''}): {body[:140]}")
    L.append("")

    # Slack
    if slack:
        L.append("*Slack mentions*")
        for s in slack:
            tag = "🤖 auto" if s.get("automated") else "🗣️ human"
            note = _strip(s.get("recap") or s.get("body") or "")[:160]
            L.append(f"• {s.get('channel','')} ({tag}) — {note}")
        L.append("")

    L.append("_Sources: Snowflake OPENCOMM (calls/texts) · FLIP_DETAILS/ACQ_L2 · AX_OFFERS (fee) · Slack. "
             "Email (transactional) + narrative RCA via Runlayer are the next additions. Verify before sharing externally._")
    return "\n".join(L)
