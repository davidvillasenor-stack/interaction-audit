from __future__ import annotations
"""
apps/interaction-audit/auto_checks.py — GROUNDED auto-verify that runs on EVERY audit (no LLM,
no key). Two kinds of check, all grounded in product_facts + THIS deal's real numbers:

  A. Numeric reconciliation — dollar/percent a rep quotes vs the deal's actual pulled value
     (service fee %, EMD $1,250, late-checkout $/day from the deal's real per-day rate).
  B. High-precision phrase rules — only fire on unambiguous wording (ALO mislabel, CP2-at-closing,
     CP1-as-full-price, roof/permit "auto-removed" claim). Deliberately conservative to avoid the
     false positives we hit grounding late-checkout against HSA Hub's $318 example.

NOT a substitute for the full LLM comprehension review (that stays on-demand / Phase-2 when a key
or Bedrock lands) — this is the automatic floor so the card is never blank/"not verified".
Output matches the accuracy_cache schema so the UI renders it identically.
"""
import re

SENT = re.compile(r"(?<=[.!?\n])\s+")

def _sentences(t):
    return [s.strip() for s in SENT.split(t or "") if s.strip()]

def _fee_pct(exp):
    m = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)\s*%", str(exp.get("fee") or ""))
    return float(m.group(1)) if m else None

def run(result: dict) -> dict:
    exp = result.get("experience", {}) or {}
    ec = result.get("economics", {}) or {}
    is_cnml = (exp.get("cnml") and exp.get("cnml") != "—") or "cash+" in (exp.get("product") or "").lower()
    fee_pct = _fee_pct(exp)                       # this offer's actual service/program fee %
    lco = ec.get("late_checkout_per_day")         # this deal's REAL per-day rate (or None)

    findings = []
    seen = set()
    def add(sev, cat, who, when, statement, verdict, correct, source):
        key = (cat, statement[:80])
        if key in seen:
            return
        seen.add(key)
        findings.append({"severity": sev, "category": cat, "who": who, "when": when,
                         "statement": statement[:400], "verdict": verdict,
                         "correct": correct, "source": source})

    for ev in result.get("events", []) or []:
        if ev.get("type") not in ("call", "text", "email"):
            continue
        body = ev.get("body") or ""
        if not body or body.lower().startswith("(no transcript"):
            continue
        who, when = ev.get("who") or "", ev.get("when") or ""
        for s in _sentences(body):
            sl = s.lower()

            # ── B. phrase rules (high precision) ──
            if re.search(r"agent listing option|agent[- ]listed offer|\blisting option\b", sl):
                add("medium", "product / ALO", who, when, s, "misstatement",
                    "ALO = Agent-Led Offer (an offer via a real estate agent), not 'Agent Listing Option'.",
                    "HSA Hub App.tsx:63")
            if re.search(r"second payment|\bcp2\b", sl) and re.search(r"at clos|up ?front|right away|immediately|at the close", sl):
                add("high", "product / CNML CP2", who, when, s, "misstatement",
                    "CP2 is paid AFTER the home resells (up to 1 year) — not at closing.",
                    "HSA Hub App.tsx:71,460")
            if re.search(r"first payment|\bcp1\b", sl) and re.search(r"full (price|amount)|100 ?%|entire (price|amount)|all (your )?(cash|money|proceeds)", sl):
                add("high", "product / CNML CP1", who, when, s, "misstatement",
                    "CP1 is ~75% of estimated sale price (an advance), not the full price; must cover the mortgage payoff.",
                    "HSA Hub App.tsx:70,452")
            if "roof" in sl and "permit" in sl and re.search(r"removed|comes? off|waiv|taken off|credit(ed)?", sl) \
               and re.search(r"auto|automatic|just|simply|guarantee|will come off|comes off completely", sl):
                add("medium", "process / repair policy", who, when, s, "unsupported policy claim",
                    "There is NO 'roof replaced within N years + permit → charge auto-removed' rule. A documented-proof DV Results Review can challenge a charge, but removal is a review, not automatic.",
                    "HSA Hub App.tsx:1704,1758")

            # ── A. numeric reconciliation vs the deal ──
            # EMD ≠ $1,250
            if re.search(r"earnest|\bemd\b|e\.m\.d", sl):
                for m in re.finditer(r"\$\s?([0-9][0-9,]{2,})", sl):
                    if m.group(1).replace(",", "") != "1250":
                        add("medium", "money / EMD", who, when, s, "amount mismatch",
                            "Earnest money deposit is $1,250 (CNML; Cash varies by market) — confirm the quoted amount.",
                            "HSA Hub App.tsx:75,464"); break
            # service/program fee % quoted that doesn't match THIS offer's actual fee
            if fee_pct is not None and re.search(r"\bfee\b|service charge|experience charge|program fee", sl):
                for m in re.finditer(r"(\d{1,2}(?:\.\d+)?)\s*%", sl):
                    try:
                        q = float(m.group(1))
                    except ValueError:
                        continue
                    if abs(q - fee_pct) > 0.5:
                        add("high" if q < fee_pct else "medium", "fees", who, when, s, "fee % mismatch",
                            f"This offer's actual fee is {fee_pct:g}%{' (program fee)' if is_cnml else ''}. Quote the customer's real fee (ideally as a dollar amount), not a different rate.",
                            "deal: this offer's FEE1_PERCENTAGE / program fee"); break
            # late-checkout $/day vs the deal's REAL per-day rate (NOT HSA Hub's $318 example)
            if lco and re.search(r"late ?check|check ?out|per day|/ ?day|daily (rate|charge|rental)", sl):
                for m in re.finditer(r"\$\s?([0-9]{2,4})", sl):
                    amt = int(m.group(1))
                    # ignore the deposit figure and near-matches to the real rate
                    if amt in (4000, 4_000):
                        continue
                    if abs(amt - lco) > max(20, 0.15 * lco) and 30 <= amt <= 1200:
                        add("medium", "late checkout", who, when, s, "daily rate mismatch",
                            f"This deal's actual late-checkout rate is ${lco}/day (WEB.TASKS_LATE_CHECKOUTS). The quoted ${amt}/day differs — confirm before quoting.",
                            "deal: economics.late_checkout_per_day"); break

    sev_rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: sev_rank.get(f["severity"], 9))
    return {
        "reviewed_at": None,   # stamped by caller
        "reviewer": "auto",
        "source": "Automated grounded checks (numeric reconciliation vs deal + high-precision rules)",
        "auto": True,
        "note": "Automatic pass — numeric + rule checks against HSA Hub facts and this deal's real numbers. Covers clear/quantitative misstatements; a full comprehension review (nuanced/paraphrased claims) runs on-demand.",
        "verified_accurate": [],
        "findings": findings,
    }
