from __future__ import annotations
"""
apps/interaction-audit/slack_search.py — live Slack search for a flip (by token AND address).

Enabled when a Slack token with search:read is set in the environment:
    export SLACK_USER_TOKEN=xoxp-...        # a user token with the search:read scope
(SLACK_TOKEN also accepted.) No token in code — never commit it.

If no token is set, the caller falls back to the per-flip cache / UI deep-link search.
"""
import os
import re
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    PHX = ZoneInfo("America/Phoenix")
except Exception:  # noqa: BLE001
    PHX = None

TOKEN = os.environ.get("SLACK_USER_TOKEN") or os.environ.get("SLACK_TOKEN")
BOT_HINTS = ("bot", "scrooge", "gumloop", "workflow", "zapier", "deal updates")

def enabled() -> bool:
    return bool(TOKEN)

def _fmt(ts: str) -> str:
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        if PHX:
            dt = dt.astimezone(PHX)
        h = dt.hour % 12 or 12
        ap = "a" if dt.hour < 12 else "p"
        return f"{dt.strftime('%b')} {dt.day}, {h}:{dt.minute:02d}{ap}"
    except Exception:  # noqa: BLE001
        return ""

def _redact(t: str) -> str:
    if not t:
        return t
    t = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "•••@•••", t)
    t = re.sub(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", "(•••) •••-••••", t)
    return t

def _search(query: str, count: int = 25) -> list[dict]:
    import requests
    try:
        r = requests.get(
            "https://slack.com/api/search.messages",
            headers={"Authorization": f"Bearer {TOKEN}"},
            params={"query": query, "count": count, "sort": "timestamp", "sort_dir": "desc"},
            timeout=20,
        )
        j = r.json()
        return j.get("messages", {}).get("matches", []) if j.get("ok") else []
    except Exception:  # noqa: BLE001
        return []

def search_flip(flip: str, address: str | None = None) -> list[dict]:
    """Search Slack for the flip token AND the property address; return UI-shaped mentions."""
    if not TOKEN:
        return []
    queries = [q for q in [flip, (address or "").strip()] if q]
    seen, matches = set(), []
    for q in queries:
        for m in _search(f'"{q}"'):
            ch = m.get("channel", {}) or {}
            key = (ch.get("id"), m.get("ts"))
            if key in seen:
                continue
            seen.add(key)
            matches.append(m)
    out = []
    for m in matches:
        ch = m.get("channel", {}) or {}
        if ch.get("is_im") or ch.get("is_mpim"):
            continue  # skip DMs only; private channels are kept (PII masked below)
        uname = (m.get("username") or m.get("user_name") or "").lower()
        is_auto = bool(m.get("bot_id")) or m.get("subtype") == "bot_message" or any(h in uname for h in BOT_HINTS)
        ts = str(m.get("ts", ""))
        out.append({
            "channel": "#" + (ch.get("name") or "channel") + (" (private)" if ch.get("is_private") else ""),
            "cid": ch.get("id") or "",
            "ts": ts.replace(".", ""),
            "when": _fmt(ts),
            "who": (m.get("user_name") or m.get("username") or "unknown") + (" (bot)" if is_auto else ""),
            "automated": is_auto,
            "issue": not is_auto,            # human posts flagged for a recap
            "body": _redact((m.get("text") or "")[:1200]),
            "recap": None,                    # generated recap is the LLM-phase upgrade
        })
    # newest first
    out.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return out
