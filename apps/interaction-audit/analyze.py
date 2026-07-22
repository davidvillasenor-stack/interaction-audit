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

PRODUCT_FACTS = [
    {"id": "fee-cash", "cat": "Fees", "fact": "Cash / Cash+ service fee is 5% of the purchase price. (It is not ~3%.)"},
    {"id": "fee-alo", "cat": "Fees", "fact": "ALO: seller pays standard agent commissions; no Opendoor service fee on the listing itself."},
    {"id": "price-neg", "cat": "Pricing", "fact": "The cash offer price is NOT negotiable — only re-evaluated with new home information, never haggled."},
    {"id": "repair-credit", "cat": "Repairs", "fact": "Post-diligence repair credits are set by the inspection scope and deducted from the offer — itemized, not negotiable."},
    {"id": "close-timeline", "cat": "Timelines", "fact": "The seller chooses the close date, typically 14–60 days out. 'As fast as 7 days' is not a standard commitment."},
    {"id": "cnml-v3", "cat": "Products", "fact": "Cash+ / CNML V3: cash advance now + an additional payment after Opendoor resells (net of costs) — not simply a higher cash price."},
]

# rule -> (factRef, sev, issue, correct)
MIS_META = {
    "fee-low":   ("fee-cash", "high", "Understated the service fee used in the customer's net comparison.", "The Cash/Cash+ service fee is 5%, not lower. Re-quote net proceeds at 5%."),
    "price-neg": ("price-neg", "med", "Implied the cash offer price is negotiable / open to a follow-up price talk.", "Opendoor cash offers aren't negotiated on price — only re-evaluated with new home info."),
    "fast-close": ("close-timeline", "med", "Promised an unusually fast close that isn't a standard commitment.", "Seller picks the close date, typically 14–60 days. Avoid promising ~7 days."),
    "repair-neg": ("repair-credit", "med", "Implied repair credits are negotiable.", "Repair credits are set by the inspection scope and itemized — not negotiable."),
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

def scan_misstatements(raw):
    prod = (raw.get("experience", {}).get("product") or "").lower()
    hits = []
    def add(rule, who, when, said):
        f, sev, issue, correct = MIS_META[rule]
        hits.append({"who": who, "when": when, "channel": "call/text", "sev": sev,
                     "said": f'"{said.strip()[:240]}"', "issue": issue, "correct": correct, "factRef": f})
    items = [("call", c) for c in raw.get("calls", [])] + [("text", t) for t in raw.get("texts", [])]
    for kind, it in items:
        text = it.get("transcript") if kind == "call" else it.get("content")
        if not text:
            continue
        who = _name_from_email(it.get("hsa_email")) + " (HSA)" if kind == "call" else "Opendoor"
        when = _fmt(_parse(it.get("when"))) + (" call" if kind == "call" else " text")
        for s in _sentences(text):
            sl = s.lower()
            # fee understatement: a % below 5 near the word 'fee'
            if "fee" in sl:
                for m in re.finditer(r"(\d(?:\.\d+)?)\s*%", sl):
                    try:
                        if float(m.group(1)) < 5:
                            add("fee-low", who, when, s); break
                    except ValueError:
                        pass
            if re.search(r"(flexib\w+ on price|negotiat\w* (the )?price|price is negotiab|come up on (the )?price|lower the price)", sl):
                add("price-neg", who, when, s)
            if re.search(r"(as fast as|close in|as quick as)\s*(a week|7 days|seven days|5 days)", sl):
                add("fast-close", who, when, s)
            if re.search(r"(negotiat\w* (the )?repair|repairs? (are|is) negotiab)", sl):
                add("repair-neg", who, when, s)
    # de-dup identical said+factRef
    seen, out = set(), []
    for h in hits:
        k = (h["factRef"], h["said"])
        if k not in seen:
            seen.add(k); out.append(h)
    return out

# ── gap / miss detection ──────────────────────────────────────────────────────
def detect_gaps(raw, comms):
    m = raw.get("milestones", {})
    pa = _parse(m.get("pa_completed"))
    dv = _parse(m.get("diligence_completed"))
    gaps = []
    # offer-ready -> first human outbound
    offer_ready = _parse(m.get("uw_completed")) or _parse(m.get("last_offer_sent"))
    first_out = next((c for c in comms if c["_dir"] == "out_human"), None)
    if offer_ready and first_out:
        hrs = (first_out["_dt"] - offer_ready).total_seconds() / 3600
        if hrs > 24:
            gaps.append({"sev": "mild", "days": round(hrs / 24, 1), "owner": "HSA",
                         "where": "Pre-contact — offer ready → first outreach",
                         "text": f"Offer ready {_fmt(offer_ready)} → first human outreach {_fmt(first_out['_dt'])} (SLA 24h)."})
    # inbound customer -> next human outbound
    for i, c in enumerate(comms):
        if c["_dir"] != "in":
            continue
        nxt = next((x for x in comms[i + 1:] if x["_dir"] == "out_human"), None)
        end = nxt["_dt"] if nxt else datetime.now(c["_dt"].tzinfo)
        hrs = (end - c["_dt"]).total_seconds() / 3600
        if hrs > 48:
            if pa and c["_dt"] > pa:
                owner, where = "Title / TC", "Post-contract — awaiting title/close"
            elif dv and c["_dt"] > dv:
                owner, where = "Sales Support / Repair desk", "Post-diligence — awaiting repair scope"
            else:
                owner, where = "HSA", "Owed a response to an inbound customer message"
            gaps.append({"sev": "", "days": round(hrs / 24, 1), "owner": owner, "where": where,
                         "text": f"Customer reached out {_fmt(c['_dt'])}; next human response {('at ' + _fmt(nxt['_dt'])) if nxt else 'never logged'} ({round(hrs/24,1)}d)."})
    # expected close passed, not closed
    exp, close = _parse(m.get("expected_close")), _parse(m.get("acq_close"))
    if exp and not close and exp < datetime.now(exp.tzinfo):
        d = round((datetime.now(exp.tzinfo) - exp).total_seconds() / 86400, 1)
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
    comms.sort(key=lambda x: x["_dt"])

    gaps = detect_gaps(raw, comms)
    mis = scan_misstatements(raw)

    # events (timeline): milestones + comms + tasks
    events = []
    m = raw.get("milestones", {})
    def mile(dtstr, tag, who, body):
        dt = _parse(dtstr)
        if dt:
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
        if dt:
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
        else:
            events.append({"_dt": c["_dt"], "type": "text",
                           "who": "Customer → Opendoor" if c["_dir"] == "in" else "Opendoor → Customer",
                           "when": _fmt(c["_dt"]), "dir": "Inbound" if c["_dir"] == "in" else "Outbound",
                           "auto": (c["_dir"] == "out_auto"), "body": r.get("content") or ""})
    events.sort(key=lambda x: x["_dt"])
    for e in events:
        e.pop("_dt", None)

    # counts + metrics for summary
    n_call = len(raw.get("calls", []))
    n_text = len(raw.get("texts", []))
    n_task = len(raw.get("tasks", []))
    wait = round(sum(g.get("days", 0) for g in gaps), 1)
    hsa = exp.get("hsa") or (_name_from_email(raw["calls"][0]["hsa_email"]) if raw.get("calls") else "—")
    summary = (f"<b>{product}{' · CNML '+cnml if cnml!='—' else ''}</b> via <b>{channel}</b>, HSA <b>{hsa}</b>. "
               f"Reached across {n_call} call(s) and {n_text} text(s). "
               + (f"<b>{len(gaps)} miss(es)</b>, total time the customer waited on us ≈ <b>{wait}d</b>. "
                  if gaps else "<b>No response gaps over 48h.</b> ")
               + (f"<b>{len(mis)} accuracy flag(s)</b> on what we told the customer. " if mis else "")
               + f"Current status: <b>{state}</b>. <i>(v0 heuristics — LLM summary/accuracy upgrade later.)</i>")

    return {
        "customer": raw.get("customer", "Customer"),
        "address": "",
        "experience": {"product": product, "cnml": cnml, "arm": exp.get("arm") or "—",
                       "channel": channel, "hsa": hsa, "state": state, "stateClass": state_cls},
        "slack": [],  # live Slack wiring optional; empty for now
        "summary": summary,
        "gaps": gaps,
        "misstatements": mis,
        "events": events,
    }
