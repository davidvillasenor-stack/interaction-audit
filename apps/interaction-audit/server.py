from __future__ import annotations
"""
apps/interaction-audit/server.py — live backend. Type ANY flip token → it queries
Snowflake on demand (your VPN + Okta SSO), analyzes, and returns the audit.

Run (on VPN; first query opens an Okta browser login):
    cd ~/batting-cage
    python3 -m uvicorn apps.interaction-audit.server:app --port 8799 --reload
    # then open http://localhost:8799   (or expose via a tunnel for a shareable link)
"""
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

app = FastAPI(title="Customer Interaction Audit")
_CACHE: dict[str, dict] = {}

INDEX = (HERE / "index_live.html")

@app.get("/", response_class=HTMLResponse)
def home():
    return INDEX.read_text() if INDEX.exists() else "<h1>index_live.html missing — run build first</h1>"

@app.get("/api/audit/{flip}")
def audit(flip: str, refresh: bool = False):
    flip = flip.strip().upper()
    if not flip:
        return JSONResponse({"error": "empty flip token"}, status_code=400)
    if flip in _CACHE and not refresh:
        return _CACHE[flip]
    try:
        raw = pull.pull_flip(flip)          # live Snowflake, redacted
        result = analyze_mod.analyze(raw)   # deterministic + rule-based
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"{type(e).__name__}: {e}", "flip_token": flip}, status_code=500)
    if result.get("error"):
        return JSONResponse(result, status_code=404)
    _CACHE[flip] = result
    return result

@app.get("/healthz")
def healthz():
    return {"ok": True}
