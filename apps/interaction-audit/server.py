from __future__ import annotations
"""
apps/interaction-audit/server.py — live backend. Type ANY flip token → it queries
Snowflake on demand (your VPN + Okta SSO), analyzes, and returns the audit.

Run (on VPN; first query opens an Okta browser login):
    cd ~/batting-cage
    python3 -m uvicorn apps.interaction-audit.server:app --port 8799 --reload
    # then open http://localhost:8799   (or expose via a tunnel for a shareable link)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # batting-cage root

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

# reuse the pull + analyze we already wrote
import importlib.util  # noqa: E402
HERE = Path(__file__).resolve().parent
def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
pull = _load("pull")
analyze_mod = _load("analyze")
slack_search = _load("slack_search")
rca_mod = _load("rca")

app = FastAPI(title="Customer Interaction Audit")
_CACHE: dict[str, dict] = {}

INDEX = (HERE / "index_live.html")

@app.get("/", response_class=HTMLResponse)
def home():
    return INDEX.read_text() if INDEX.exists() else "<h1>index_live.html missing — run build first</h1>"

def _assemble(flip: str) -> dict:
    """Pull + analyze + attach flip_token + Slack. Returns the full audit result (or {error})."""
    raw = pull.pull_flip(flip)
    result = analyze_mod.analyze(raw)
    if result.get("error"):
        return result
    result["flip_token"] = flip
    try:
        if slack_search.enabled():
            result["slack"] = slack_search.search_flip(flip, result.get("address"))
        else:
            sc = HERE / "slack_cache" / f"{flip}.json"
            if sc.exists():
                result["slack"] = json.loads(sc.read_text())
    except Exception:  # noqa: BLE001
        pass
    return result

@app.get("/api/audit/{q:path}")
def audit(q: str, refresh: bool = False):
    q = (q or "").strip()
    if not q:
        return JSONResponse({"error": "empty query"}, status_code=400)
    try:
        flip = pull.resolve_flip(q)         # flip token OR property address
        if not flip:
            return JSONResponse({"error": f"No flip or address match for '{q}'"}, status_code=404)
        if flip in _CACHE and not refresh:
            return _CACHE[flip]
        result = _assemble(flip)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"{type(e).__name__}: {e}", "query": q}, status_code=500)
    if result.get("error"):
        return JSONResponse(result, status_code=404)
    _CACHE[flip] = result
    return result

@app.get("/api/rca/{q:path}")
def rca(q: str):
    """One-click Case Summary & RCA → generate from the audit and DM it to David."""
    q = (q or "").strip()
    if not q:
        return JSONResponse({"error": "empty query"}, status_code=400)
    try:
        flip = pull.resolve_flip(q)
        if not flip:
            return JSONResponse({"error": f"No flip or address match for '{q}'"}, status_code=404)
        result = _CACHE.get(flip) or _assemble(flip)
        if result.get("error"):
            return JSONResponse(result, status_code=404)
        _CACHE[flip] = result
        text = rca_mod.build_rca(result, flip)
        delivery = slack_search.deliver_dm(text)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"{type(e).__name__}: {e}", "query": q}, status_code=500)
    return {"flip_token": flip, "delivered": delivery.get("delivered"),
            "via": delivery.get("via"), "error": delivery.get("error"), "rca": text}

@app.get("/healthz")
def healthz():
    return {"ok": True}
