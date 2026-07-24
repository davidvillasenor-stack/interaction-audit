# Customer Interaction Audit — Hackathon Submission

**Author:** David Villasenor · National Manager, Partnerships Management (HSA team)
**Event:** Opendoor Hackathon — due Fri Jul 24 2026, 5 PM
**Submission = demo video + public repo link.**

---

## What it is

Drop in a **flip token** → one clean, reconstructed view of *everything* that happened to that
customer across the whole transaction, and — critically — **whether we told them anything wrong.**

- **Full timeline** — every call (with transcript), text, email, task, and milestone, in order.
  Calls collapse to a one-line "📞 Phone call · 5m"; long texts/emails collapse too — click to
  expand. Lists cap at 30 with a "+30" expander so it never becomes a wall of text.
- **Where we went dark & who we were waiting on** — response-gap detection with **owner
  attribution** (HSA / Sales Support / TC / Title).
- **Accuracy review — auto-verified on every audit.** Grounded in **HSA Hub (primary source of
  truth) + cx-knowledge + the deal's real numbers**, it flags where a rep told the customer
  something inaccurate — fee %, EMD, late-checkout rate, CNML CP1/CP2 timing, ALO mislabels,
  unsupported repair-credit claims — each with the source it violated. Never a false "all clear":
  it shows flags, "verified accurate," or an honest "not yet reviewed."
- **Deal economics** — product (Cash / Cash+/CNML), current headline price, **fee in dollars**,
  and for Cash+ the **CP1 (cash at closing) / CP2 (after-resale upside)** split + advance rate.
- **Search + filters** across every message/transcript — keyword, date range, and by sender.
- **Live Zendesk email**, **Slack mentions** of the property/flip, and an **Ops Hub deep-link**.

**Audience:** Sales Support, HSAs, TCs, XAs, and leaders — anyone who needs to reconstruct a
customer's journey fast: to coach, resolve an escalation, or audit an SLA miss or misstatement.

## Why it matters

Reconstructing "what happened with this seller" today means stitching together City, Casey,
OpenComm, Zendesk, and Slack by hand — minutes to hours per case — and misstatements go uncaught
entirely. This collapses it to **one flip token and a few seconds**, and answers the two things a
manager actually cares about: *where did we drop the ball, and did we tell the customer anything
wrong?* On a live deal it already surfaced a rep quoting an unsupported roof-credit "policy" and a
seller who asked three times for a repair itemization, never got it, and walked to a competitor.

## How it works

- `pull.py` — resolves flip → customer (and rolls forward to the property's current real flip),
  pulls calls/texts/tasks/milestones + deal economics + **live Zendesk email**; redacts identifiers.
- `analyze.py` — deterministic timeline + response-gap detection with owner attribution + summary.
- **`auto_checks.py` — the accuracy engine.** Numeric reconciliation (every $/%/date a rep quotes
  vs the deal's *actual* pulled numbers) + high-precision product-fact rules. Runs on **every**
  audit, no LLM/key. `product_facts.py` + `grounding.py` assemble the grounded fact base +
  per-deal pack; a deeper on-demand LLM review supersedes the auto pass per flip when run.
- `zendesk_api.py` — live Zendesk REST pull (cookie auth) → current email, seller vs internal/ops.
- `server.py` — FastAPI (`/api/audit`, `/api/grounding`, `/api/rca`); `index_live.html` — the UI.
- `template.html` — self-contained fictional sample page (no setup needed).

## Tech stack

Python (FastAPI + uvicorn), deterministic analysis + grounded accuracy engine (**no API key
required**). Snowflake (live, SSO/Okta). Live Zendesk API. Slack (per-flip). Self-contained HTML UI.

## Privacy / safety

Redaction on by default (names → initials; phone/email masked). **No customer data or credentials
in the repo** — records pulled live at runtime; `raw/`, `slack_cache/`, `accuracy_cache/`,
`data.json`, `.env` are git-ignored. Demo runs on **localhost + screen-share** (message/transcript
bodies can still contain names/addresses — body-scrubbing is the gate before any public live link).

## Demo tokens (all live)

- **52NKJYH9PAEYX** — Cash+/CNML (CP1 $121,927), 11 calls; auto-flags a roof/permit claim.
- **23Z2M89TGMZ72** — Cash; deep grounded review incl. a real transparency-gap finding.
- **76PJFZSRDSYCA / 3D2AYM3YDKXQ9** — Cash+/CNML with CP1/CP2 economics.

Run: `bash ~/batting-cage/apps/interaction-audit/run.sh` → http://localhost:8799 (VPN + Okta).

## Links

- **🎥 Demo video:** https://www.loom.com/share/2494c6099b744b1d9fb0221a57a845b8
- **💻 Public repo (submit this):** https://github.com/davidvillasenor-stack/interaction-audit
- Private org mirror: https://github.com/opendoor-labs/interaction-audit

## What's next

- **Full LLM auto-verify** — the auto pass catches clear/quantitative misstatements today; a real
  Anthropic key or Opendoor Bedrock upgrades it to catch nuanced/paraphrased claims automatically.
- **Slack auto-pull** for any flip (Slack user token, `search:read`).
- **Body-scrubbing** of message bodies → enables a hosted, SSO-gated company-wide deployment.
