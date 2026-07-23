from __future__ import annotations
"""
apps/interaction-audit/grounding.py — assemble the per-flip GROUNDING PACK for the accuracy
review. Fuses the grounded fact base (product_facts) with THIS deal's real numbers + the comms
to check, so a review (Claude on-demand now; Bedrock later) scrubs statements against reality —
not against 7 sample facts.

Not a flat lookup: the pack carries source precedence, documented conflicts, sanctioned-script
exceptions, and the numeric-reconciliation targets from the deal's actual Snowflake data.
"""
import product_facts as PF


def _fmt_money(v):
    try:
        return "$" + format(int(v), ",")
    except (TypeError, ValueError):
        return "—"


def build_grounding_pack(result: dict) -> dict:
    """result = the analyzed audit dict (analyze.analyze). Returns a structured grounding pack."""
    exp = result.get("experience", {}) or {}
    ec = result.get("economics", {}) or {}
    is_cnml = (exp.get("cnml") and exp.get("cnml") != "—") or "cash+" in (exp.get("product") or "").lower()

    # THIS deal's real numbers — what every quoted figure gets reconciled against.
    deal = {
        "flip_token": result.get("flip_token"),
        "address": result.get("address"),
        "product": exp.get("product"),
        "is_cnml": bool(is_cnml),
        "status": exp.get("state"),
        "hsa": exp.get("hsa"),
        "service_fee_pct": exp.get("fee"),
        "service_fee_usd": _fmt_money(exp.get("fee_usd")) if exp.get("fee_usd") is not None else "—",
        "fee_name": exp.get("fee_name"),
        "headline_price": _fmt_money(ec.get("cnml_headline") if is_cnml and ec.get("cnml_headline") else ec.get("offer_price")),
        "purchase_price": _fmt_money(ec.get("purchase_price")),
        "net_proceeds": _fmt_money(ec.get("net_price")),
        "cp1_cash_at_closing": _fmt_money(ec.get("cp1")) if is_cnml else "n/a (Cash)",
        "cp2_upside_after_resale": _fmt_money(ec.get("cp2")) if is_cnml else "n/a (Cash)",
        "advance_rate_pct": (str(ec.get("adv_rate_pct")) + "%") if ec.get("adv_rate_pct") is not None else "—",
        "dv_repair_charge": _fmt_money(ec.get("dv_repairs")),
        "ai_scoped_repairs": _fmt_money(ec.get("ai_repairs")),
        "late_checkout_per_day": (_fmt_money(ec.get("late_checkout_per_day")) + "/day (this deal's actual rate)") if ec.get("late_checkout_per_day") is not None else "not set for this deal (quote from TASKS_LATE_CHECKOUTS, not HSA Hub's $318)",
        "emd_expected": "$1,250 (CNML)" if is_cnml else "varies (Cash)",
    }

    # The statements to review — calls (transcripts) + texts + emails, in order.
    comms = []
    for e in result.get("events", []) or []:
        if e.get("type") not in ("call", "text", "email"):
            continue
        body = (e.get("body") or "").strip()
        if not body or body.lower().startswith("(no transcript"):
            continue
        comms.append({
            "type": e.get("type"), "when": e.get("when"), "who": e.get("who"),
            "dir": e.get("dir"), "subject": e.get("subj"), "text": body,
        })

    return {
        "deal": deal,
        "facts": PF.FACTS,
        "synonyms": PF.SYNONYMS,
        "exceptions": PF.EXCEPTIONS,
        "conflicts": PF.CONFLICTS,
        "numeric_checks": PF.NUMERIC_CHECKS,
        "facts_source": PF.FACTS_SOURCE,
        "comms": comms,
        "review_method": [
            "1. Extract every assertion the rep makes about product / process / fees / timeline / "
            "late checkout / how-we-work / economics (not keyword matching — real claims).",
            "2. Check each against the fact base (HSA Hub primary → cx-knowledge cross-ref). Honor "
            "`conflicts` (newest/most-specific wins) and `exceptions` (don't flag sanctioned scripts).",
            "3. Reconcile every dollar / % / date against `deal` per `numeric_checks`.",
            "4. Adversarially try to REFUTE each candidate flag before surfacing it; keep only survivors. "
            "Output each as {statement, verdict, correct, source, severity}.",
        ],
    }


def render_markdown(pack: dict) -> str:
    d = pack["deal"]
    L = []
    L.append(f"# Accuracy grounding pack — flip {d.get('flip_token')}")
    L.append(f"_Source of truth: {pack['facts_source']}_\n")
    L.append("## THIS deal's real numbers (reconcile every quote against these)")
    for k, v in d.items():
        L.append(f"- **{k}**: {v}")
    L.append("\n## Grounded facts (HSA Hub primary · cx-knowledge cross-ref)")
    for f in pack["facts"]:
        fr = f" · updated {f['freshness']}" if f.get("freshness") else ""
        L.append(f"- [{f['cat']} · tier {f['tier']}{fr}] {f['fact']}  \n  _src: {f['source']}_")
    L.append("\n## Documented conflicts (resolution rules)")
    for c in pack["conflicts"]:
        L.append(f"- {c}")
    L.append("\n## Sanctioned-script exceptions (do NOT flag)")
    for e in pack["exceptions"]:
        L.append(f"- {e}")
    L.append("\n## Numeric reconciliation targets")
    for n in pack["numeric_checks"]:
        L.append(f"- **{n['claim']}** → {n['field']} — {n['rule']}")
    L.append(f"\n## Communications to review ({len(pack['comms'])})")
    for c in pack["comms"]:
        head = f"[{c['type']} · {c['when']} · {c.get('who') or ''} {c.get('dir') or ''}]"
        L.append(f"\n{head}\n{c['text']}")
    L.append("\n## Review method")
    for step in pack["review_method"]:
        L.append(f"- {step}")
    return "\n".join(L)
