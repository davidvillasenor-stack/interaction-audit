from __future__ import annotations
"""
apps/interaction-audit/zendesk_api.py — LIVE Zendesk email pull.

Copies superdojo's zendesk-mcp auth pattern (services/zendesk-mcp/.../utils/auth.py):
cookie-based, file-first. Reads session cookies from ~/.zendesk-mcp-auth.json (written
by refresh-zendesk-cookies.py, Playwright/Okta), falls back to pycookiecheat Chrome
extraction. Hits the LIVE Zendesk REST API (opendoor.zendesk.com/api/v2) — current data,
NOT the stale FIVETRAN.ZENDESK Snowflake mirror (which ends ~Feb 2026).

To enable: `python3 ~/.claude/scripts/refresh-zendesk-cookies.py` (add --headed once if it
needs Okta MFA). No API token needed — session cookies, same as the MCP.
"""
import json, os, re
import requests

AUTH_FILE = os.path.expanduser("~/.zendesk-mcp-auth.json")
ZENDESK_URL = os.environ.get("ZENDESK_URL", "https://opendoor.zendesk.com")
API = f"{ZENDESK_URL}/api/v2"
FLIP_TOKEN_FIELD = 9707317021979   # custom field id (from zendesk-mcp constants.py)

_cookies_cache = None

def _load_cookies() -> dict:
    # 1) file written by refresh-zendesk-cookies.py (preferred)
    if os.path.exists(AUTH_FILE):
        try:
            c = (json.load(open(AUTH_FILE)) or {}).get("cookies") or {}
            if c:
                return c
        except Exception:  # noqa: BLE001
            pass
    # 2) fallback: pull straight from Chrome (only if pycookiecheat is installed)
    try:
        from pycookiecheat import chrome_cookies
        return chrome_cookies(ZENDESK_URL) or {}
    except Exception:  # noqa: BLE001
        return {}

def _cookies() -> dict:
    global _cookies_cache
    if _cookies_cache is None:
        _cookies_cache = _load_cookies()
    return _cookies_cache

def enabled() -> bool:
    """True when we have Zendesk session cookies (i.e. the live pull can run)."""
    return bool(_cookies())

def _get(path: str, params: dict | None = None) -> dict:
    r = requests.get(f"{API}{path}", cookies=_cookies(), params=params or {},
                     headers={"Content-Type": "application/json"}, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_emails(customer_email: str | None = None, flip_token: str | None = None,
                 redact=None, limit: int = 200) -> list[dict]:
    """Live-pull the customer's email tickets + comments from Zendesk.
    Matches tickets by requester email AND by the flip-token custom field, then returns
    every comment shaped like the rest of the audit's `emails` list. Returns [] if auth
    is missing or the API errors (email is additive — never fail the whole audit on it)."""
    if not enabled():
        return []
    tickets: dict[int, dict] = {}
    kind: dict[int, str] = {}   # ticket_id -> "seller" | "ops"
    def _search(q, k):
        try:
            for t in _get("/search.json", {"query": q}).get("results", []):
                tid = t.get("id")
                if not tid:
                    continue
                tickets[tid] = t
                if k == "seller" or kind.get(tid) != "seller":   # seller wins if matched both ways
                    kind[tid] = k
        except Exception:  # noqa: BLE001
            pass
    if customer_email:
        _search(f'type:ticket requester:{customer_email}', "seller")   # true customer correspondence
    if flip_token:
        _search(f'type:ticket fieldvalue:{flip_token}', "ops")         # property tickets (HOA/title/ops)
    out = []
    for tid, t in tickets.items():
        try:
            comments = _get(f"/tickets/{tid}/comments.json").get("comments", [])
        except Exception:  # noqa: BLE001
            continue
        subject = t.get("subject") or "(no subject)"
        requester_id = t.get("requester_id")
        k = kind.get(tid, "ops")
        for c in comments:
            body = (c.get("plain_body") or c.get("body") or "").strip()
            if not body:
                continue
            inbound = (c.get("author_id") is not None and c.get("author_id") == requester_id)
            out.append({
                "id": c.get("id"),
                "when": c.get("created_at"),
                "direction": "inbound" if inbound else "outbound",
                "subject": subject,
                "is_public": bool(c.get("public")),
                "kind": k,                         # "seller" vs "ops" — surfaced as a badge in the UI
                "body": redact(body) if redact else body,
            })
    out.sort(key=lambda e: e.get("when") or "")
    return out[:limit]
