from __future__ import annotations
"""
apps/interaction-audit/pull.py — pull a customer's full interaction record for one or
more flip tokens, from Snowflake, REDACTING direct identifiers as it writes.

Reuses the blessed flip->customer matching (ax_leads.initial_customer_id + web.customers
household_id/human_id + last-10-digit phone) and the call_lookup transcript pattern.

Output: raw/<FLIP_TOKEN>.json  — one file per flip, redacted, ready for analysis.
Email is NOT pulled (not in OpenComm; Phase 2 = Zendesk). Slack mentions are gathered
separately (via the Slack connector) at analysis time.

Usage:
    # on VPN, Okta SSO (first query opens a browser login)
    python3 apps/interaction-audit/pull.py 5N2WC7B6QRCF4 7K4XB2M9WQPL8 ...
    python3 apps/interaction-audit/pull.py --file tokens.txt
    python3 apps/interaction-audit/pull.py --no-redact 5N2WC7B6QRCF4   # internal SSO host only
"""
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from shared.snowflake_client import query  # noqa: E402

OUT = Path(__file__).resolve().parent / "raw"
OUT.mkdir(exist_ok=True)
REDACT = "--no-redact" not in sys.argv

# ── redaction helpers ─────────────────────────────────────────────────────────
def initials(name: str | None) -> str:
    if not name:
        return "Customer"
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    return " ".join(p[0].upper() + "." for p in parts[:3]) or "Customer"

def mask_phone(num: str | None) -> str:
    if not num:
        return ""
    d = re.sub(r"[^0-9]", "", num)
    return f"(•••) •••-{d[-4:]}" if len(d) >= 4 else "•••"

def mask_addr(addr: str | None) -> str:
    if not addr:
        return ""
    # keep house-number prefix + city/state, mask the street name and zip tail
    segs = [s.strip() for s in addr.split(",")]
    street = segs[0]
    m = re.match(r"^(\d{1,2})(\d*)\s+(.*)$", street)
    if m:
        street = f"{m.group(1)}{'•'*max(len(m.group(2)),2)} {re.sub(r'[A-Za-z]', '•', m.group(3))[:1]}••••• {street.split()[-1]}"
    out = [street] + segs[1:-1] + ([re.sub(r'\d', '•', segs[-1])] if len(segs) > 1 else [])
    return ", ".join(out)

def red_name(n): return initials(n) if REDACT else (n or "Customer")
def red_phone(n): return mask_phone(n) if REDACT else (n or "")
def red_addr(a): return mask_addr(a) if REDACT else (a or "")

# ── SQL ───────────────────────────────────────────────────────────────────────
RESOLVE = """
with lead as (
  select al.id lead_id, al.flip_id, al.flip_token, al.initial_customer_id,
         al.phone_number, al.market_name
  from DWH.dw.ax_leads al where al.flip_token = %(flip)s
),
li as (select wc.uuid init_uuid, wc.household_id, wc.human_id,
         coalesce(wc.first_name,'') fn, coalesce(wc.last_name,'') ln
  from lead l left join DWH.web.customers wc on wc.id = l.initial_customer_id),
au as (select distinct wc2.uuid cu from li join DWH.web.customers wc2
  on (li.human_id is not null and wc2.human_id = li.human_id)
  or (li.household_id is not null and wc2.household_id = li.household_id))
select
  (select flip_token from lead) flip_token,
  (select flip_id from lead) flip_id,
  (select market_name from lead) market_name,
  (select trim(fn||' '||ln) from li) cust_name,
  (select right(regexp_replace(phone_number,'[^0-9]',''),10) from lead) ph10,
  array_construct((select init_uuid from li)) init_uuids,
  (select array_agg(cu) from au) hh_uuids
"""

# calls + sms pulled by customer_id list OR phone10 (built in Python)
CALLS_TMPL = """
select pc.ID id, pc.CREATED_AT created_at, pc.CALL_DURATION_SECONDS dur,
       pc.DISPOSITION disp, pc.TRANSCRIPT transcript, pc.DIRECTION direction,
       pc.FROM_PHONE_NUMBER frm, pc.TO_PHONE_NUMBER too,
       flex.HANDLING_WORKER_EMAIL hsa_email
from DWH.opencomm.dwh_phone_calls_view pc
left join DWH.opencomm.dwh_twilio_flex_calls_view flex
       on flex.opencomm_phone_call_uuid = pc.id
where ({where})
order by pc.CREATED_AT
"""

SMS_TMPL = """
select s.ID id, s.CREATED_AT created_at, s.DIRECTION direction, s.CONTENT content,
       s.FROM_NUMBER frm, s.TO_NUMBER too,
       cc.is_automated is_automated
from DWH.opencomm.dwh_sms_messages_view s
left join DWH.opencomm.dwh_communication_context_view cc on cc.communication_id = s.id
where ({where})
order by s.CREATED_AT
"""

TASKS = """
with off as (select uuid from DWH.web.offers where flip_id = %(flip_id)s)
select t.task_type, t.active_at
from DWH.CASEY.DWH_TASKS_VIEW t
join DWH.CASEY.DWH_RELATED_OBJECTS_VIEW r on r.task_uuid = t.uuid and r.object_type='offer'
where r.object_id in (select uuid from off)
order by t.active_at
"""

FLIP = """
select fd.FLIP_STATE, fd.ACQ_CX, fd.MARKET_NAME, fd.LISTING_OFFER_CHANNEL,
       fd.FIRST_OFFER_SENT_AT, fd.LAST_OFFER_SENT_AT,
       fd.PURCHASE_AGREEMENT_COMPLETED_AT, fd.ACTUAL_CONTINGENCIES_RELEASE_DATE,
       fd.EXPECTED_ACQUISTION_CLOSE_DATE, fd.ACQUISITION_CLOSE_DATE,
       acq.UW_COMPLETED_AT, acq.DILIGENCE_COMPLETED_AT, acq.IS_WALKED, acq.WALK_PRIMARY_REASON
from DWH.DATA_MART_FRONTEND.FLIP_DETAILS fd
left join DWH.ACQUISITION.ACQ_L2_FLIP_DETAILS acq on acq.flip_token = fd.flip_token
where fd.FLIP_TOKEN = %(flip)s
limit 1
"""

ARM = """
select max(case when f.key='sales-26-02-experiment-incremental' then f.value:treatment::string end) arm,
       max(case when f.key='hot_seller_lead' then f.value:treatment::string end) tier
from DWH.web.offers o, table(flatten(input=>parse_json(o.experiments))) f
where o.flip_id = %(flip_id)s and o.experiments is not null
"""

def _sql_list(vals):
    return ",".join("'" + str(v).replace("'", "''") + "'" for v in vals if v)

def comms_where(uuids, ph10):
    parts = []
    if uuids:
        parts.append(f"CUSTOMER_ID in ({_sql_list(uuids)})")
    if ph10:
        parts.append(f"right(regexp_replace(TO_PHONE_NUMBER,'[^0-9]',''),10) = '{ph10}'")
        parts.append(f"right(regexp_replace(FROM_PHONE_NUMBER,'[^0-9]',''),10) = '{ph10}'")
    return " or ".join(parts) if parts else "1=0"

def sms_where(uuids, ph10):
    parts = []
    if uuids:
        parts.append(f"CUSTOMER_ID in ({_sql_list(uuids)})")
    if ph10:
        parts.append(f"right(regexp_replace(TO_NUMBER,'[^0-9]',''),10) = '{ph10}'")
        parts.append(f"right(regexp_replace(FROM_NUMBER,'[^0-9]',''),10) = '{ph10}'")
    return " or ".join(parts) if parts else "1=0"

def iso(x):
    return x.isoformat() if hasattr(x, "isoformat") else (str(x) if x is not None else None)

def pull_flip(flip: str) -> dict:
    r = query(RESOLVE, {"flip": flip})
    if not r:
        return {"flip_token": flip, "error": "flip not found in ax_leads"}
    row = r[0]
    flip_id = row.get("FLIP_ID")
    init = [u for u in (json.loads(row["INIT_UUIDS"]) if isinstance(row.get("INIT_UUIDS"), str) else (row.get("INIT_UUIDS") or [])) if u]
    hh = [u for u in (json.loads(row["HH_UUIDS"]) if isinstance(row.get("HH_UUIDS"), str) else (row.get("HH_UUIDS") or [])) if u]
    uuids = sorted(set(init + hh))
    ph10 = row.get("PH10")

    calls = query(CALLS_TMPL.format(where=comms_where(uuids, ph10)))
    sms = query(SMS_TMPL.format(where=sms_where(uuids, ph10)))
    tasks = query(TASKS, {"flip_id": flip_id}) if flip_id else []
    fd = query(FLIP, {"flip": flip})
    fd = fd[0] if fd else {}
    arm = query(ARM, {"flip_id": flip_id}) if flip_id else []
    arm = arm[0] if arm else {}

    return {
        "flip_token": flip,
        "customer": red_name(row.get("CUST_NAME")),
        "market": fd.get("MARKET_NAME") or row.get("MARKET_NAME"),
        "experience": {
            "product": None,  # not on FLIP_DETAILS; derived in analyze (default Cash / ALO)
            "is_alo": None,
            "channel": fd.get("LISTING_OFFER_CHANNEL"),
            "partner": None,
            "arm": arm.get("ARM"),
            "tier": arm.get("TIER"),
            "hsa": fd.get("ACQ_CX"),
            "flip_state": fd.get("FLIP_STATE"),
        },
        "milestones": {
            "last_offer_sent": iso(fd.get("LAST_OFFER_SENT_AT") or fd.get("FIRST_OFFER_SENT_AT")),
            "uw_completed": iso(fd.get("UW_COMPLETED_AT")),
            "pa_completed": iso(fd.get("PURCHASE_AGREEMENT_COMPLETED_AT")),
            "diligence_completed": iso(fd.get("DILIGENCE_COMPLETED_AT")),
            "contingencies_released": iso(fd.get("ACTUAL_CONTINGENCIES_RELEASE_DATE")),
            "expected_close": iso(fd.get("EXPECTED_ACQUISTION_CLOSE_DATE")),
            "acq_close": iso(fd.get("ACQUISITION_CLOSE_DATE")),
            "is_walked": fd.get("IS_WALKED"),
            "walk_reason": fd.get("WALK_PRIMARY_REASON"),
        },
        "calls": [{
            "id": c["ID"], "when": iso(c["CREATED_AT"]), "dur_s": c.get("DUR"),
            "direction": c.get("DIRECTION"), "disposition": c.get("DISP"),
            "hsa_email": c.get("HSA_EMAIL"),
            "from": red_phone(c.get("FRM")), "to": red_phone(c.get("TOO")),
            "transcript": c.get("TRANSCRIPT"),  # kept intact (David's call)
        } for c in calls],
        "texts": [{
            "id": s["ID"], "when": iso(s["CREATED_AT"]), "direction": s.get("DIRECTION"),
            "is_automated": bool(s.get("IS_AUTOMATED")),
            "from": red_phone(s.get("FRM")), "to": red_phone(s.get("TOO")),
            "content": s.get("CONTENT"),
        } for s in sms],
        "tasks": [{"type": t["TASK_TYPE"], "when": iso(t["ACTIVE_AT"])} for t in tasks],
        "_redacted": REDACT,
    }

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--file" in sys.argv:
        i = sys.argv.index("--file")
        args = Path(sys.argv[i + 1]).read_text().split()
    if not args:
        print("Usage: pull.py <FLIP_TOKEN> [FLIP_TOKEN ...] | --file tokens.txt [--no-redact]")
        sys.exit(1)
    for flip in args:
        flip = flip.strip().upper()
        print(f"Pulling {flip} ...", flush=True)
        try:
            data = pull_flip(flip)
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR on {flip}: {e}")
            continue
        p = OUT / f"{flip}.json"
        p.write_text(json.dumps(data, indent=2, default=str))
        n_c, n_t, n_k = len(data.get("calls", [])), len(data.get("texts", [])), len(data.get("tasks", []))
        print(f"  → {p.name}: {n_c} calls, {n_t} texts, {n_k} tasks  (redacted={data.get('_redacted')})")

if __name__ == "__main__":
    main()
