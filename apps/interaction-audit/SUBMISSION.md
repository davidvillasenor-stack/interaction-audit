# Customer Interaction Audit — Hackathon Submission

**Author:** David Villasenor · National Manager, Partnerships Management (HSA team)
**Event:** Opendoor Hackathon — due Fri Jul 24 2026, 5 PM
**Submission = demo video + public repo link.**

---

## What it is

Drop in a **flip token** → get one clean view of *everything* that happened to that customer
across the entire transaction:

- **Full timeline** — every call (with transcript), text, task, and milestone, channel-tagged
  and in order.
- **Where we went dark and who we were waiting on** — response-gap detection with **owner
  attribution** (HSA / Sales Support / TC / Title), so a miss points at the team that owned it.
- **Misstatements vs. product facts** — a rule-based scan that flags where an Opendoor person
  told the customer something inaccurate, each flag citing the specific product fact it
  violated (ALO = Agent-Led Offer, CNML/CP1/CP2 timing, EMD = $1,250, per-offer fee mismatch).
- **Keyword search across every message and transcript** — find any phrase the customer or a
  rep ever said.
- **Slack mentions** of the property/flip — deep-linked, with recaps for real content.
- **Audit summary** — total wait-on-us, # of misses, longest gap, channels used, accuracy
  flags, per-offer fee, and the customer's experience/channel card.

**Audience:** Sales Support, HSAs, TCs, XAs, and leaders — anyone who needs to reconstruct a
customer's journey fast, whether to coach, resolve an escalation, or audit an SLA miss.

## Why it matters

Today, reconstructing "what happened with this seller" means stitching together City, Casey,
OpenComm, Zendesk, and Slack by hand — minutes-to-hours per case, and misses/misstatements go
uncaught. This collapses it to **one flip token and a few seconds**, and surfaces the two
things a manager actually cares about: *where did we drop the ball, and did we tell the
customer anything wrong?*

## How it works

- **Runs on real flip tokens today** — not demo-only data.
- `pull.py` — resolves flip → customer (initial customer + household/human_id + phone match),
  then pulls calls, texts, tasks, milestones, and Zendesk email; **redacts as it writes**
  (names → initials; phone/address/email masked).
- `analyze.py` — **deterministic** timeline + response-gap detection with owner attribution +
  rule-based misstatement checks vs. a `PRODUCT_FACTS` source of truth + summary. **No LLM
  required** (nothing to hallucinate, nothing to key/bill).
- `server.py` — FastAPI; `GET /api/audit/{flip_token}`.
- `index_live.html` — the live UI (type a token → live Snowflake pull → audit).
- `template.html` — a self-contained fictional sample page (Maria / James / Aisha) that
  demos the full UX with **no setup**.

## Tech stack

- **Python** (FastAPI + uvicorn), deterministic analysis engine — no API key, no LLM.
- **Snowflake** (live, via SSO/Okta; `keyring` caches the session so it's one login).
- Sources: `DWH.OPENCOMM` (calls/transcripts + texts), CASEY tasks, `ACQ_L2_FLIP_DETAILS`
  (UW/diligence/walk/close dates), `WEB.OFFERS` (per-offer fee), FIVETRAN.ZENDESK (email),
  Slack deep-links.
- Self-contained HTML UI — no build system, no framework.

## Privacy / safety

- **Redaction on by default** — names → initials, phone/address/email masked.
- **No customer data or credentials in the repo** — records are pulled live at runtime;
  `.gitignore` blocks `raw/` and real `data.json`. The public repo ships only code + a
  fictional sample page.
- Known open item: message/transcript *bodies* can still contain names/addresses — that's why
  the demo runs on **localhost + screen-share**, not a public tunnel.

## Demo

- Live server: `bash ~/batting-cage/apps/interaction-audit/run.sh` → http://localhost:8799
  (on VPN + Okta).
- Rich demo token: **52NKJYH9PAEYX** (11 calls / 28 texts / 62 tasks — St. Petersburg FL).
- No-setup UX walkthrough: open `template.html` in a browser.

## Links

- **Public repo (submit this):** https://github.com/davidvillasenor-stack/interaction-audit
- **Preferred repo (pending org-admin flip → Public):** https://github.com/opendoor-labs/interaction-audit
- **Demo video:** recorded at http://localhost:8799 on real flip tokens.

## What's next

- **Email (Zendesk)** fully folded into the timeline (in progress).
- **LLM-generated summary + accuracy checks** (needs org Anthropic/Bedrock access).
- **Hosted behind SSO** for company-wide use (needs a Snowflake service account / key-pair
  for headless runs).
- **Body-scrubbing** of message/transcript bodies before any public live link.
