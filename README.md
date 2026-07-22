# Customer Interaction Audit

Drop in a **flip token** → see everything that happened to the customer in one clean view:
the customer experience/channel, a **timeline of the entire transaction** (calls, texts,
tasks, milestones), **where we went dark and who we were waiting on** (HSA / Sales Support /
TC / Title), **misstatements** where an Opendoor person told the customer something
inaccurate (checked against a product-facts source of truth), plus **full keyword search**
across every message and transcript, and **Slack mentions** of the property/flip.

Built for the Opendoor Hackathon. Runs off **actual flip tokens** — no demo-only data.

> **No customer data or credentials are in this repo.** Real records are pulled live from
> Snowflake at runtime and are **redacted** (names → initials; phone/address/email masked).
> This repo contains only the code + a fictional sample page.

## See it without any setup
Open **`apps/interaction-audit/template.html`** in a browser — a self-contained page with
three fictional sample journeys that demonstrates the full UX (timeline, gap/owner
attribution, misstatements vs. product facts, message/transcript search, Slack recaps).

## Run it live (Opendoor, on VPN)
```bash
export SNOWFLAKE_ACCOUNT=...          # your Snowflake account
export SNOWFLAKE_WAREHOUSE=...        # a warehouse you can use
bash apps/interaction-audit/run.sh    # → http://localhost:8799
```
Open http://localhost:8799, type any flip token → it queries Snowflake live (first query
opens an Okta SSO login) and returns the audit.

## Architecture
- `apps/interaction-audit/pull.py` — flip → customer resolution (initial customer +
  household/human_id + phone match) → pulls calls, texts, tasks, milestones; **redacts** as it writes.
- `apps/interaction-audit/analyze.py` — deterministic timeline + response-gap detection
  (with owner attribution) + rule-based misstatement checks vs. product facts + summary. No LLM required.
- `apps/interaction-audit/server.py` — FastAPI; `GET /api/audit/{flip_token}`.
- `apps/interaction-audit/index_live.html` — the UI (fetches the API live).
- `apps/interaction-audit/template.html` — self-contained sample demo.
- `shared/snowflake_client.py` — minimal, **env-configured** Snowflake helper (no creds in code).

## Roadmap
- Email (Zendesk) into the timeline · LLM-generated summary + accuracy checks ·
  hosted behind SSO for company-wide use.
