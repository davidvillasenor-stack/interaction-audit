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
         al.phone_number, al.market_name, al.email
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
  (select email from lead) cust_email,
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
select fd.FLIP_STATE, fd.ACQ_CX, fd.MARKET_NAME, fd.LISTING_OFFER_CHANNEL, fd.ADDRESS_TOKEN,
       fd.FIRST_OFFER_SENT_AT, fd.LAST_OFFER_SENT_AT,
       fd.PURCHASE_AGREEMENT_COMPLETED_AT, fd.ACTUAL_CONTINGENCIES_RELEASE_DATE,
       fd.EXPECTED_ACQUISTION_CLOSE_DATE, fd.ACQUISITION_CLOSE_DATE,
       acq.ADDRESS_FULL, acq.UW_COMPLETED_AT, acq.DILIGENCE_COMPLETED_AT,
       acq.IS_WALKED, acq.WALK_PRIMARY_REASON
from DWH.DATA_MART_FRONTEND.FLIP_DETAILS fd
left join DWH.ACQUISITION.ACQ_L2_FLIP_DETAILS acq on acq.flip_token = fd.flip_token
where fd.FLIP_TOKEN = %(flip)s
limit 1
"""

EMAIL = """
with ids as (
  -- Zendesk stores ALL of a user's emails in USER_IDENTITY (the primary USER.email often
  -- differs from the address a seller emails from). custom_customer_email is empty in the
  -- feed, so match by identity → requester. (⚠️ FIVETRAN.ZENDESK sync is stale to ~Feb 2026;
  -- recent flips will have identities but no tickets until the sync is refreshed.)
  select distinct user_id from FIVETRAN.ZENDESK.USER_IDENTITY
  where type='email' and lower(value) in ({emails})
),
t as (
  select ID, SUBJECT, REQUESTER_ID
  from FIVETRAN.ZENDESK.TICKET
  where REQUESTER_ID in (select user_id from ids)
     or lower(CUSTOM_CUSTOMER_EMAIL) in ({emails})
)
select c.ID id, c.CREATED created_at, c.PLAIN_BODY body, c.PUBLIC is_public,
       c.USER_ID author_id, t.SUBJECT subject, t.REQUESTER_ID requester_id,
       au.ROLE author_role
from FIVETRAN.ZENDESK.TICKET_COMMENT c
join t on t.ID = c.TICKET_ID
left join FIVETRAN.ZENDESK."USER" au on au.ID = c.USER_ID
where c.PLAIN_BODY is not null
order by c.CREATED
limit 200
"""

ARM = """
select max(case when f.key='sales-26-02-experiment-incremental' then f.value:treatment::string end) arm,
       max(case when f.key='hot_seller_lead' then f.value:treatment::string end) tier
from DWH.web.offers o, table(flatten(input=>parse_json(o.experiments))) f
where o.flip_id = %(flip_id)s and o.experiments is not null
"""

# latest offer's actual per-offer fees (customer-facing). Percentages are FRACTIONS (0.0499 = 4.99%).
FEE = """
select o.number_of_fees, o.fee1_name, o.fee1_percentage, o.fee2_name, o.fee2_percentage,
       o.fee3_name, o.fee3_percentage, o.fee4_name, o.fee4_percentage,
       o.recorded_price_cents, o.net_price_cents, o.arv_cents, o.external_list_price_cents,
       o.offered_repairs_charge_cents, o.repair_costs_estimate_cents, o.valuation_cents
from DWH.web.offers o
where o.flip_id = %(flip_id)s and o.fee1_percentage is not null
order by o.created_at desc nulls last
limit 1
"""

# purchase price + post-diligence repair charge from AX_OFFERS (by address_token, latest offer)
ECON = """
select PRICE_PURCHASE, PRICE_OFFER_NET, OFFERED_REPAIRS_CHARGE_USD, REPAIR_COST_SELLER_CHARGED
from DWH.dw.ax_offers where address_token = %(at)s
order by id desc
limit 1
"""

# CNML / Cash+ ("Sell With Us"): definitive product flag + CP1/CP2 economics.
# CASH_PLUS_INDICATOR is the contract-level truth; SWU_L1_FUNNEL holds the CP1 (cash at
# closing) / CP2 (after-resale upside) split, keyed by flip_token (one row per offer).
CASHPLUS = "select cash_plus_indicator ind from DWH.CONSUMER.ACQUISITION_CONTRACTS_L1 where flip_token=%(f)s limit 1"
SWU = """
select cp1_amt, cp1_amt_before_dv,
       projected_resale_seller_upside_proceeds upside, cptotal_amt,
       headline_price, adv_rate_pct, program_fee_pct, od_program_fee,
       swu_converted, acquistion_channel, partner_name
from DWH.CONSUMER.SWU_L1_FUNNEL
where flip_token=%(f)s
order by (case when state='accepted' then 0 else 1 end), offer_sent_date desc nulls last
limit 1
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

ADDR_LOOKUP = """
select FLIP_TOKEN
from DWH.ACQUISITION.ACQ_L2_FLIP_DETAILS
where ADDRESS_FULL is not null and upper(ADDRESS_FULL) like upper(%(q)s)
order by FLIP_CREATED_AT desc nulls last
limit 1
"""

# Given any flip token, find the property it belongs to (address_token) and return the
# property's CURRENT REAL flip. A property often has several parallel flips with identical
# offer timestamps (simultaneous offer/product variants) — only one actually progresses.
# So "most recent" = the flip that advanced furthest: prefer a completed purchase agreement
# (most recent), then non-dead flips (not expired/denied/withdrawn), then offer recency.
# (David's call: audit always follows the property's real current flip.)
NEWEST_FOR_PROPERTY = """
select fd.flip_token
from DWH.DATA_MART_FRONTEND.FLIP_DETAILS fd
where fd.address_token = (
    select address_token from DWH.DATA_MART_FRONTEND.FLIP_DETAILS
    where flip_token = %(f)s and address_token is not null limit 1
)
order by
  (case when fd.purchase_agreement_completed_at is not null then 0 else 1 end),
  fd.purchase_agreement_completed_at desc nulls last,
  (case when fd.flip_state ilike '%%withdraw%%' or fd.flip_state ilike '%%expired%%'
             or fd.flip_state ilike '%%denied%%' then 1 else 0 end),
  coalesce(fd.last_offer_sent_at, fd.first_offer_sent_at) desc nulls last
limit 1
"""

def newest_flip_for_property(flip: str) -> str | None:
    """Resolve a flip token to the newest flip token for the same property (address_token)."""
    r = query(NEWEST_FOR_PROPERTY, {"f": flip})
    return r[0]["FLIP_TOKEN"] if r else None

def resolve_flip(q: str) -> str | None:
    """Accept a flip token (primary) or a property address (fallback); always return the
    MOST RECENT flip token for that property."""
    q = (q or "").strip()
    if not q:
        return None
    # looks like a flip token → confirm it exists, then roll forward to the property's newest flip
    if re.fullmatch(r"[A-Za-z0-9]{8,20}", q):
        u = q.upper()
        exists = (query("select flip_token from DWH.dw.ax_leads where flip_token=%(q)s limit 1", {"q": u})
                  or query("select token from DWH.web.flips where token=%(q)s limit 1", {"q": u}))
        if exists:
            return newest_flip_for_property(u) or u
    # otherwise treat as an address → newest matching flip → then property's newest flip
    r = query(ADDR_LOOKUP, {"q": f"%{q}%"})
    if r:
        tok = r[0]["FLIP_TOKEN"]
        return newest_flip_for_property(tok) or tok
    return None

def pull_flip(flip: str) -> dict:
    r = query(RESOLVE, {"flip": flip})
    if not r:
        return {"flip_token": flip, "error": "flip not found in ax_leads"}
    row = r[0]
    flip_id = row.get("FLIP_ID")
    if not flip_id:  # early-stage flip not in the analytics marts → resolve flip_id operationally
        wf = query("select id from DWH.web.flips where token=%(f)s limit 1", {"f": flip})
        flip_id = wf[0]["ID"] if wf else None
    def _arr(v):
        # Snowflake arrays come back as JSON strings; array_construct(NULL) → "[undefined]" (invalid JSON)
        if isinstance(v, list):
            return [x for x in v if x]
        if isinstance(v, str):
            for s in (v, v.replace("undefined", "null")):
                try:
                    return [x for x in json.loads(s) if x]
                except Exception:  # noqa: BLE001
                    continue
        return []
    init = _arr(row.get("INIT_UUIDS"))
    hh = _arr(row.get("HH_UUIDS"))
    uuids = sorted(set(init + hh))
    ph10 = row.get("PH10")

    calls = query(CALLS_TMPL.format(where=comms_where(uuids, ph10)))
    sms = query(SMS_TMPL.format(where=sms_where(uuids, ph10)))
    tasks = query(TASKS, {"flip_id": flip_id}) if flip_id else []
    fd = query(FLIP, {"flip": flip})
    fd = fd[0] if fd else {}
    arm = query(ARM, {"flip_id": flip_id}) if flip_id else []
    arm = arm[0] if arm else {}

    # ── per-offer fee (customer-facing; each offer is unique) ──
    feerow = query(FEE, {"flip_id": flip_id}) if flip_id else []
    fr = feerow[0] if feerow else {}
    def _pct(x):
        try:
            return round(float(x) * 100, 2)
        except (TypeError, ValueError):
            return None
    offer_fee = None
    if fr:
        total = 0.0
        for i in range(1, 5):
            v = fr.get(f"FEE{i}_PERCENTAGE")
            if v is not None:
                total += float(v)
        price_for_fee = None
        try:
            price_for_fee = float(fr.get("RECORDED_PRICE_CENTS")) / 100
        except (TypeError, ValueError):
            price_for_fee = None
        svc_frac = fr.get("FEE1_PERCENTAGE")
        offer_fee = {
            "service_name": fr.get("FEE1_NAME"),
            "service_pct": _pct(svc_frac),
            "total_pct": round(total * 100, 2),
            # dollar amounts we actually charge (fee % applied to the headline/recorded price)
            "service_usd": round(price_for_fee * float(svc_frac)) if (price_for_fee and svc_frac is not None) else None,
            "total_usd": round(price_for_fee * total) if price_for_fee else None,
            "n_fees": fr.get("NUMBER_OF_FEES"),
        }
    def _usd(cents):
        try:
            return round(float(cents) / 100)
        except (TypeError, ValueError):
            return None
    def _rnd(v):
        try:
            return round(float(v))
        except (TypeError, ValueError):
            return None
    # AX_OFFERS (by address_token) → purchase price + post-diligence repair charge
    address_token = fd.get("ADDRESS_TOKEN")
    axo = {}
    if address_token:
        axr = query(ECON, {"at": address_token})
        axo = axr[0] if axr else {}
    offer_price = _usd(fr.get("RECORDED_PRICE_CENTS")) if fr else None
    economics = {
        "offer_price": offer_price,                                    # headline offer presented
        "purchase_price": _rnd(axo.get("PRICE_PURCHASE")) or offer_price,  # final/contract price
        "net_price": _rnd(axo.get("PRICE_OFFER_NET")) or (_usd(fr.get("NET_PRICE_CENTS")) if fr else None),
        "ai_repairs": _rnd(axo.get("OFFERED_REPAIRS_CHARGE_USD")) or (_usd(fr.get("REPAIR_COSTS_ESTIMATE_CENTS")) if fr else None),
        "dv_repairs": _rnd(axo.get("REPAIR_COST_SELLER_CHARGED")),      # post-diligence charge to seller
        "list_price": _usd(fr.get("EXTERNAL_LIST_PRICE_CENTS")) if fr else None,
    }

    # ── CNML / Cash+ detection + CP1/CP2 (overrides product + fee when it's a Cash+ deal) ──
    product = None
    try:
        cpr = query(CASHPLUS, {"f": flip})
        is_cnml = bool(cpr) and str(cpr[0].get("IND")).lower() == "yes"
    except Exception:  # noqa: BLE001
        is_cnml = False
    swu = {}
    if is_cnml:
        try:
            sr = query(SWU, {"f": flip})
            swu = sr[0] if sr else {}
        except Exception:  # noqa: BLE001
            swu = {}
        product = "Cash+"
        economics["cp1"] = _rnd(swu.get("CP1_AMT"))                                  # cash at closing
        economics["cp2"] = _rnd(swu.get("UPSIDE"))                                   # after-resale upside ("more later")
        economics["cp_total"] = _rnd(swu.get("CPTOTAL_AMT"))                         # projected total proceeds
        economics["cnml_headline"] = _rnd(swu.get("HEADLINE_PRICE"))                 # resale headline (not the cash number)
        economics["adv_rate_pct"] = round(float(swu["ADV_RATE_PCT"]) * 100, 1) if swu.get("ADV_RATE_PCT") is not None else None
        # Cash+ customer-facing fee = program fee (not the Opendoor Experience %)
        pf, pfp = swu.get("OD_PROGRAM_FEE"), swu.get("PROGRAM_FEE_PCT")
        if pf is not None:
            offer_fee = {"service_name": "Cash+ Program Fee",
                         "service_pct": round(float(pfp) * 100, 2) if pfp is not None else None,
                         "service_usd": abs(round(float(pf))), "total_usd": abs(round(float(pf))),
                         "total_pct": round(float(pfp) * 100, 2) if pfp is not None else None, "n_fees": 1}

    # ── email (Zendesk via FIVETRAN) by customer email ──
    emails_rows = []
    cust_email = row.get("CUST_EMAIL")
    if cust_email:
        elist = _sql_list([cust_email.lower()])
        try:
            emails_rows = query(EMAIL.format(emails=elist))
        except Exception:  # noqa: BLE001 — email is additive; never fail the whole audit on it
            emails_rows = []
    emails = []
    for e in emails_rows:
        cust = (e.get("AUTHOR_ID") is not None and e.get("AUTHOR_ID") == e.get("REQUESTER_ID")) \
            or (e.get("AUTHOR_ROLE") or "").lower() == "end-user"
        emails.append({
            "id": e["ID"], "when": iso(e.get("CREATED_AT")),
            "direction": "inbound" if cust else "outbound",
            "subject": e.get("SUBJECT") or "(no subject)",
            "is_public": bool(e.get("IS_PUBLIC")),
            "body": e.get("BODY"),
        })

    return {
        "flip_token": flip,
        "customer": red_name(row.get("CUST_NAME")),
        "address": fd.get("ADDRESS_FULL"),  # shown (needed for Slack-by-address search)
        "market": fd.get("MARKET_NAME") or row.get("MARKET_NAME"),
        "experience": {
            "product": product,  # "Cash+" when CNML detected; else None → analyze defaults Cash/ALO
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
        "offer_fee": offer_fee,
        "economics": economics,
        "emails": emails,
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
