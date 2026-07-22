from __future__ import annotations
"""
apps/interaction-audit/analyze.py — turn a raw pull (pull.pull_flip) into the UI dict
the frontend renders. Deterministic timeline/gaps/experience/counts + rule-based
misstatement detection (v0 heuristics vs PRODUCT_FACTS) + a templated summary.

No LLM required — runs live inside the server for any flip token.
"""
import re
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    PHX = ZoneInfo("America/Phoenix")
except Exception:  # noqa: BLE001
    PHX = None

# Product facts — SOURCED FROM HSA HUB (projects.simplersell.com/hsa-hub), the live source
# of truth for current products/pilots. Only assert what the source states.
FACTS_SOURCE = "HSA Hub (projects.simplersell.com/hsa-hub)"
PRODUCT_FACTS = [
    {"id": "alo", "cat": "Products", "fact": "ALO = Agent-Led Offer — an offer generated through a real estate agent. (Not 'Agent Listing Option'.)"},
    {"id": "cnml", "cat": "Products", "fact": "CNML = Cash Now, More Later — upfront cash now (CP1) plus a second payment (CP2) after Opendoor resells."},
    {"id": "cp1", "cat": "Products", "fact": "CP1 (First Payment, CNML) = upfront cash at closing, ~75% of estimated sale price — NOT the full price. It must cover the seller's mortgage payoff, or CNML isn't a fit."},
    {"id": "cp2", "cat": "Products", "fact": "CP2 (Second Payment, CNML) = additional proceeds paid after the home resells, up to 1 year later — not at closing."},
    {"id": "fo", "cat": "Process", "fact": "FO = Final Offer, presented after underwriting. DV = Diligence Visit (via Inspectify), AFTER the contract is signed."},
    {"id": "emd", "cat": "Process", "fact": "EMD (Earnest Money Deposit) = $1,250 deposited with the escrow agent."},
    {"id": "fee", "cat": "Fees", "fact": "There is NO blanket fee — each offer's fee is unique. Verify fee claims against the customer's actual offer, not a fixed %."},
]

# rule -> (factRef, sev, issue, correct) — every rule maps to a fact above (all hsa-hub-sourced)
MIS_META = {
    "alo-mislabel": ("alo", "med", "Mislabeled ALO to the customer.", "ALO = Agent-Led Offer (an offer via a real estate agent), not 'Agent Listing Option'."),
    "cnml-cp1":     ("cp1", "high", "Overstated the CNML first payment (CP1).", "CP1 is ~75% of estimated sale price (not full/100%), and must cover the mortgage payoff."),
    "cp2-timing":   ("cp2", "med", "Misstated CNML second-payment (CP2) timing.", "CP2 is paid AFTER the home resells (up to 1 year) — not at closing."),
    "emd-amount":   ("emd", "med", "Stated an incorrect earnest money amount.", "EMD is $1,250."),
}

def _parse(s):
    if not s:
        return None
    dt = None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        try:
            dt = datetime.fromisoformat(str(s)[:19])
        except Exception:  # noqa: BLE001
            return None
    if dt is not None and dt.tzinfo is None:  # normalize naive → UTC-aware so all compares/sorts work
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def _phx(dt):
    if dt is None:
        return None
    try:
        if PHX and dt.tzinfo:
            return dt.astimezone(PHX)
    except Exception:  # noqa: BLE001
        pass
    return dt

def _fmt(dt):
    dt = _phx(dt)
    if dt is None:
        return ""
    h = dt.hour % 12 or 12
    ap = "a" if dt.hour < 12 else "p"
    return f"{dt.strftime('%b')} {dt.day}, {h}:{dt.minute:02d}{ap}"

def _name_from_email(e):
    if not e:
        return "Opendoor"
    local = e.split("@")[0]
    return " ".join(p.capitalize() for p in re.split(r"[._]", local))

def _dur(sec):
    if not sec:
        return ""
    m = int(sec) // 60
    return f" · {m}m" if m else " · <1m"

# ── misstatement scan ─────────────────────────────────────────────────────────
def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", text or "") if s.strip()]

def scan_misstatements(raw, win_start=None, win_end=None):
    prod = (raw.get("experience", {}).get("product") or "").lower()
    of = raw.get("offer_fee") or {}
    svc = of.get("service_pct")  # this offer's actual service fee %, or None
    hits = []
    def add(rule, who, when, said):
        f, sev, issue, correct = MIS_META[rule]
        hits.append({"who": who, "when": when, "channel": "call/text", "sev": sev,
                     "said": f'"{said.strip()[:240]}"', "issue": issue, "correct": correct, "factRef": f})
    items = ([("call", c) for c in raw.get("calls", [])]
             + [("text", t) for t in raw.get("texts", [])]
             + [("email", e) for e in raw.get("emails", [])])
    for kind, it in items:
        if win_start and win_end:
            dt = _parse(it.get("when"))
            if dt is None or not (win_start <= dt <= win_end):
                continue
        text = it.get("transcript") if kind == "call" else (it.get("body") if kind == "email" else it.get("content"))
        if not text:
            continue
        who = _name_from_email(it.get("hsa_email")) + " (HSA)" if kind == "call" else "Opendoor"
        when = _fmt(_parse(it.get("when"))) + (" call" if kind == "call" else (" email" if kind == "email" else " text"))
        for s in _sentences(text):
            sl = s.lower()
            # ALO mislabeled (source of truth: ALO = Agent-Led Offer)
            if re.search(r"agent listing option|agent[- ]listed offer|listing option", sl):
                add("alo-mislabel", who, when, s)
            # CNML first payment (CP1) overstated as full/100%
            if (("cnml" in sl or "cash now more later" in sl or "first payment" in sl or "cp1" in sl)
                    and re.search(r"full (price|amount)|100%|entire (price|amount)|all (your )?(cash|money|proceeds)", sl)):
                add("cnml-cp1", who, when, s)
            # CP2 timing (paid after resale, not at closing)
            if re.search(r"second payment|cp2", sl) and re.search(r"at closing|up ?front|right away|immediately|at the close", sl):
                add("cp2-timing", who, when, s)
            # EMD amount ≠ $1,250
            if re.search(r"earnest|emd", sl):
                for m in re.finditer(r"\$\s?([0-9][0-9,]{2,})", sl):
                    if m.group(1).replace(",", "") != "1250":
                        add("emd-amount", who, when, s); break
            # per-offer fee mismatch: a % quoted near fee/charge that differs from THIS offer's actual fee
            if svc is not None and re.search(r"fee|service charge|experience charge", sl):
                for m in re.finditer(r"(\d{1,2}(?:\.\d+)?)\s*%", sl):
                    try:
                        q = float(m.group(1))
                    except ValueError:
                        continue
                    if abs(q - svc) > 0.5:
                        hits.append({
                            "who": who, "when": when, "channel": kind,
                            "sev": "high" if q < svc else "med",
                            "said": f'"{s.strip()[:240]}"',
                            "issue": f"Quoted a {q:g}% fee, but this offer's actual service fee is {svc:g}%.",
                            "correct": f"This offer's service fee is {svc:g}% ({of.get('service_name') or 'Opendoor Experience'}). Quote the customer's actual offer, not a fixed rate.",
                            "factRef": "fee"})
                        break
    # de-dup identical said+factRef
    seen, out = set(), []
    for h in hits:
        k = (h["factRef"], h["said"])
        if k not in seen:
            seen.add(k); out.append(h)
    return out

# ── gap / miss detection ──────────────────────────────────────────────────────
def detect_gaps(raw, comms, win_start, win_end):
    """Gaps within the analysis window only. Open gaps are capped at win_end (never 'now'),
    so a long-closed deal doesn't produce a multi-year phantom gap."""
    m = raw.get("milestones", {})
    pa = _parse(m.get("pa_completed"))
    dv = _parse(m.get("diligence_completed"))
    gaps = []
    # offer-ready -> first human outbound (only if the offer-ready is inside the window)
    offer_ready = _parse(m.get("uw_completed")) or _parse(m.get("last_offer_sent"))
    first_out = next((c for c in comms if c["_dir"] == "out_human"), None)
    if offer_ready and first_out and win_start and offer_ready >= win_start and first_out["_dt"] >= offer_ready:
        hrs = (first_out["_dt"] - offer_ready).total_seconds() / 3600
        if hrs > 24:
            gaps.append({"sev": "mild", "days": round(hrs / 24, 1), "owner": "HSA",
                         "where": "Pre-contact — offer ready → first outreach",
                         "text": f"Offer ready {_fmt(offer_ready)} → first human outreach {_fmt(first_out['_dt'])} (SLA 24h)."})
    # inbound customer -> next human outbound (open gap capped at win_end)
    for i, c in enumerate(comms):
        if c["_dir"] != "in":
            continue
        nxt = next((x for x in comms[i + 1:] if x["_dir"] == "out_human"), None)
        end = nxt["_dt"] if nxt else win_end
        if not end or end < c["_dt"]:
            continue
        hrs = (end - c["_dt"]).total_seconds() / 3600
        if hrs > 48:
            if pa and c["_dt"] > pa:
                owner, where = "Title / TC", "Post-contract — awaiting title/close"
            elif dv and c["_dt"] > dv:
                owner, where = "Sales Support / Repair desk", "Post-diligence — awaiting repair scope"
            else:
                owner, where = "HSA", "Owed a response to an inbound customer message"
            gaps.append({"sev": "", "days": round(hrs / 24, 1), "owner": owner, "where": where,
                         "text": f"Customer reached out {_fmt(c['_dt'])}; next human response {('at ' + _fmt(nxt['_dt'])) if nxt else 'not within this window'} ({round(hrs/24,1)}d)."})
    # expected close passed, not closed — only if expected close falls inside the window
    exp, close = _parse(m.get("expected_close")), _parse(m.get("acq_close"))
    if exp and not close and win_start and win_start <= exp <= win_end:
        d = round((win_end - exp).total_seconds() / 86400, 1)
        if d > 0:
            gaps.append({"sev": "", "days": d, "owner": "Title / TC",
                         "where": "Post-contract — past expected close, not closed",
                         "text": f"Expected close {_fmt(exp)} has passed with no acquisition close on record."})
    return gaps

# ── main ──────────────────────────────────────────────────────────────────────
def analyze(raw):
    if raw.get("error"):
        return {"error": raw["error"], "flip_token": raw.get("flip_token")}
    exp = raw.get("experience", {})
    product = exp.get("product") or ("ALO" if exp.get("is_alo") else "Cash")
    cnml = "V3" if (product or "").lower() in ("cash+", "cash plus", "cnml") else "—"
    channel = exp.get("channel") or "—"
    if exp.get("partner"):
        channel = f"{channel} ({exp['partner']})"
    of = raw.get("offer_fee") or {}
    fee_disp = f"{of['service_pct']:g}%" if of.get("service_pct") is not None else "—"
    state = (exp.get("flip_state") or "").replace("_", " ").title() or "—"
    good = any(k in (exp.get("flip_state") or "") for k in ("closed", "released", "acq_close"))
    withdrawn = "withdraw" in (exp.get("flip_state") or "")
    state_cls = "state-warn" if withdrawn else ("state-good" if good else "")

    # unified comms (calls + texts) with normalized direction + dt
    comms = []
    for c in raw.get("calls", []):
        dt = _parse(c.get("when"))
        if not dt:
            continue
        d = (c.get("direction") or "").lower()
        comms.append({"_dt": dt, "_kind": "call",
                      "_dir": "in" if "in" in d else "out_human",
                      "raw": c})
    for t in raw.get("texts", []):
        dt = _parse(t.get("when"))
        if not dt:
            continue
        d = (t.get("direction") or "").lower()
        if "in" in d:
            dd = "in"
        else:
            dd = "out_auto" if t.get("is_automated") else "out_human"
        comms.append({"_dt": dt, "_kind": "text", "_dir": dd, "raw": t})
    for e in raw.get("emails", []):
        dt = _parse(e.get("when"))
        if not dt:
            continue
        d = (e.get("direction") or "").lower()
        comms.append({"_dt": dt, "_kind": "email", "_dir": "in" if "in" in d else "out_human", "raw": e})
    comms.sort(key=lambda x: x["_dt"])

    # ── analysis window: last ~6 months of interactions; if nothing recent, anchor on the
    # most recent activity and look back 6 months from there. Avoids multi-year phantom gaps.
    WIN_DAYS = 180
    now_ref = datetime.now(timezone.utc)
    if comms:
        recent = [c for c in comms if (now_ref - c["_dt"]).days <= WIN_DAYS]
        if recent:
            win_end, win_start = now_ref, now_ref - timedelta(days=WIN_DAYS)
        else:
            last_act = max(c["_dt"] for c in comms)
            win_end, win_start = last_act, last_act - timedelta(days=WIN_DAYS)
        comms = [c for c in comms if win_start <= c["_dt"] <= win_end]
    else:
        win_end, win_start = now_ref, now_ref - timedelta(days=WIN_DAYS)

    def _inwin(dtstr):
        dt = _parse(dtstr)
        return dt is not None and win_start <= dt <= win_end

    gaps = detect_gaps(raw, comms, win_start, win_end)
    mis = scan_misstatements(raw, win_start, win_end)

    # events (timeline): milestones + comms + tasks
    events = []
    m = raw.get("milestones", {})
    def mile(dtstr, tag, who, body):
        dt = _parse(dtstr)
        if dt and win_start <= dt <= win_end:
            events.append({"_dt": dt, "type": "milestone", "tag": tag, "who": who, "when": _fmt(dt), "body": body})
    mile(m.get("last_offer_sent"), "Offer sent", "Final offer sent", f"{product} offer sent.")
    mile(m.get("pa_completed"), "Contract", "Purchase agreement signed", "PA completed.")
    mile(m.get("diligence_completed"), "Diligence", "Diligence visit completed", "DV completed.")
    mile(m.get("contingencies_released"), "Cont. released", "Contingencies released", "Contingencies released.")
    mile(m.get("acq_close"), "Closed", "Acquisition closed", "Deal closed.")
    if m.get("is_walked"):
        mile(m.get("acq_close") or m.get("expected_close"), "Withdrawn",
             "Walked / withdrawn", f"Walk reason: {m.get('walk_reason') or 'n/a'}.")
    for t in raw.get("tasks", []):
        dt = _parse(t.get("when"))
        if dt and win_start <= dt <= win_end:
            events.append({"_dt": dt, "type": "task", "who": f"{(t.get('type') or 'task').replace('_',' ').title()} — task", "when": _fmt(dt), "body": f"OpsHub/CASEY task_type: {t.get('type')}."})
    for c in comms:
        r = c["raw"]
        if c["_kind"] == "call":
            events.append({"_dt": c["_dt"], "type": "call",
                           "who": f"{_name_from_email(r.get('hsa_email'))} · call",
                           "when": _fmt(c["_dt"]),
                           "dir": ("Inbound" if c["_dir"] == "in" else "Outbound") + _dur(r.get("dur_s")),
                           "auto": False, "meta": f"transcript · {r.get('disposition') or ''}",
                           "body": r.get("transcript") or "(no transcript)"})
        elif c["_kind"] == "email":
            events.append({"_dt": c["_dt"], "type": "email",
                           "who": "Customer → Opendoor" if c["_dir"] == "in" else "Opendoor → Customer",
                           "when": _fmt(c["_dt"]), "dir": "Inbound" if c["_dir"] == "in" else "Outbound",
                           "auto": False, "subj": r.get("subject"), "body": r.get("body") or ""})
        else:
            events.append({"_dt": c["_dt"], "type": "text",
                           "who": "Customer → Opendoor" if c["_dir"] == "in" else "Opendoor → Customer",
                           "when": _fmt(c["_dt"]), "dir": "Inbound" if c["_dir"] == "in" else "Outbound",
                           "auto": (c["_dir"] == "out_auto"), "body": r.get("content") or ""})
    events.sort(key=lambda x: x["_dt"])
    for e in events:
        e["iso"] = e["_dt"].isoformat()   # keep a sortable timestamp for the UI (newest-first + 45d window)
        e.pop("_dt", None)

    # counts + metrics for summary (windowed)
    n_call = sum(1 for c in comms if c["_kind"] == "call")
    n_text = sum(1 for c in comms if c["_kind"] == "text")
    n_email = sum(1 for c in comms if c["_kind"] == "email")
    n_task = sum(1 for t in raw.get("tasks", []) if _inwin(t.get("when")))
    wait = round(sum(g.get("days", 0) for g in gaps), 1)
    hsa = exp.get("hsa") or (_name_from_email(raw["calls"][0]["hsa_email"]) if raw.get("calls") else "—")
    summary = (f"<b>{product}{' · CNML '+cnml if cnml!='—' else ''}</b> via <b>{channel}</b>, HSA <b>{hsa}</b>. "
               f"Reached across {n_call} call(s), {n_text} text(s), and {n_email} email(s). "
               + (f"<b>{len(gaps)} miss(es)</b>, total time the customer waited on us ≈ <b>{wait:.1f}d</b>. "
                  if gaps else "<b>No response gaps over 48h.</b> ")
               + (f"<b>{len(mis)} accuracy flag(s)</b> on what we told the customer. " if mis else "")
               + (f"This offer's service fee: <b>{fee_disp}</b>. " if fee_disp != "—" else "")
               + f"Current status: <b>{state}</b>. "
               + f"<i>(Window: {_fmt(win_start)} – {_fmt(win_end)}, last ~6 months of activity.)</i>")

    return {
        "customer": raw.get("customer", "Customer"),
        "address": raw.get("address") or "",
        "economics": raw.get("economics") or {},
        "experience": {"product": product, "cnml": cnml, "arm": exp.get("arm") or "—",
                       "channel": channel, "hsa": hsa, "state": state, "stateClass": state_cls,
                       "fee": fee_disp},
        "slack": [],  # live Slack wiring optional; empty for now
        "summary": summary,
        "gaps": gaps,
        "misstatements": mis,
        "events": events,
    }
