# Customer Interaction Audit — prototype

Drop in a flip token → see everything that happened to the customer (calls, texts, tasks,
milestones, Slack), where the misses/gaps were and **who we were waiting on**, any
**misstatements vs. product facts**, plus full transcript search. Self-contained HTML,
no backend — bakes real (redacted) data in at build time.

## Friday-hackathon pipeline (no IT)

1. **Pull** (on VPN + Okta SSO — first query opens a browser login):
   ```bash
   cd ~/batting-cage
   python3 apps/interaction-audit/pull.py 5N2WC7B6QRCF4 7K4XB2M9WQPL8 ...
   # writes apps/interaction-audit/raw/<FLIP>.json  (redacted: names→initials, phone/addr/email masked)
   ```
2. **Analyze** — Claude reads `raw/*.json` (+ pulls Slack mentions via the Slack connector)
   and writes `data.json` (the UI-ready DATA dict: summary, gaps w/ owner, misstatements
   vs. product facts, Slack recaps).
3. **Build**:
   ```bash
   python3 apps/interaction-audit/build.py apps/interaction-audit/data.json
   # writes apps/interaction-audit/index.html  (banner switches to "REAL DATA — REDACTED")
   ```
4. **Share** — push `index.html` to a GitHub Pages repo → public link.

## Files
- `template.html`  — the UI (sample data). `/*__DATA_START__*/../*__DATA_END__*/` markers = injection point.
- `pull.py`        — Snowflake extract + redaction (reuses shared/snowflake_client + blessed flip→customer match).
- `build.py`       — injects `data.json` into the template → `index.html`.
- `raw/`           — pulled per-flip JSON (redacted). git-ignored in spirit; do not commit real data.
- `data.json`      — analyzed, UI-ready (built by Claude from raw/). do not commit real data.

## Notes
- Redaction is **on by default**. `--no-redact` only for an SSO-gated internal host.
- Email is Phase 2 (Zendesk). Live any-token lookup + LLM summaries need the service
  account + org Anthropic access (separate IT track).
- Do **not** commit `raw/` or real `data.json` to a public repo — only the built,
  redacted `index.html` goes to GitHub Pages.
