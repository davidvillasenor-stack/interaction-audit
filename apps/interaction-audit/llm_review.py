from __future__ import annotations
"""
apps/interaction-audit/llm_review.py — FULL comprehension accuracy review via Gemini.

When GOOGLE_API_KEY (or GEMINI_API_KEY) is set, this runs automatically on every audit: it feeds
the grounded pack (grounding.build_grounding_pack — HSA Hub facts + cx-knowledge + THIS deal's
real numbers + the comms) to Gemini and gets back structured findings. Catches nuanced/paraphrased
misstatements the deterministic auto_checks pass can't. Falls back to auto_checks on any error, so
it never breaks an audit. Output matches the accuracy_cache schema.

Get a FREE key at https://aistudio.google.com/app/apikey → put GOOGLE_API_KEY in .env.
"""
import os

MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

def _key():
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

def enabled() -> bool:
    return bool(_key())

_SCHEMA = {
    "type": "object",
    "properties": {
        "verified_accurate": {"type": "array", "items": {"type": "string"}},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "category": {"type": "string"},
                    "who": {"type": "string"},
                    "when": {"type": "string"},
                    "statement": {"type": "string"},
                    "verdict": {"type": "string"},
                    "correct": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["severity", "category", "statement", "verdict", "correct", "source"],
            },
        },
    },
    "required": ["verified_accurate", "findings"],
}

_INSTRUCTIONS = """You are an accuracy auditor for Opendoor. Below is a GROUNDING PACK for one
deal: the source-of-truth product facts (HSA Hub is primary; cx-knowledge cross-ref), documented
cross-source CONFLICTS with resolution rules, sanctioned-script EXCEPTIONS you must NOT flag,
numeric-reconciliation targets, THIS deal's REAL numbers, and the actual communications (calls,
texts, emails) between Opendoor and the customer.

Your job: find only REAL inaccuracies an Opendoor rep told the customer.
Rules:
- Extract each factual claim a rep makes (product, process, fees, timeline, late checkout,
  how-we-work, economics). Ignore the customer's own statements.
- Check each against the grounded facts. Honor the CONFLICTS (newest/most-specific wins) and the
  EXCEPTIONS (never flag a sanctioned script).
- Reconcile every dollar/percent/date against THIS deal's real numbers — NOT against example
  figures. (e.g. late-checkout rate = the deal's real per-day rate, not a generic example.)
- Be adversarial: before keeping a flag, try to refute it. If a quote actually MATCHES the deal's
  data or the facts, put it in verified_accurate instead of findings. Do not invent issues.
- severity: high = materially wrong money/terms; medium = misleading/process; low = coaching.
- For each finding, cite the specific fact/number it violates in `source`.
Return JSON only, matching the schema."""

def run(pack: dict) -> dict | None:
    """Run the Gemini review on a grounding pack dict. Returns accuracy dict, or None on failure
    (caller falls back to the deterministic auto pass)."""
    if not enabled():
        return None
    try:
        import json
        from google import genai
        from google.genai import types
        import grounding  # same-dir; render the pack as the model-facing brief
        brief = grounding.render_markdown(pack)
        client = genai.Client(api_key=_key())
        resp = client.models.generate_content(
            model=MODEL,
            contents=f"{_INSTRUCTIONS}\n\n===== GROUNDING PACK =====\n{brief}",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_SCHEMA,
                temperature=0,
            ),
        )
        data = json.loads(resp.text)
        findings = data.get("findings", []) or []
        rank = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda f: rank.get(f.get("severity"), 9))
        return {
            "reviewed_at": None,   # stamped by caller
            "reviewer": f"Gemini ({MODEL})",
            "source": "Gemini grounded review — HSA Hub + cx-knowledge + this deal's real numbers",
            "auto": False,
            "llm": True,
            "verified_accurate": data.get("verified_accurate", []) or [],
            "findings": findings,
        }
    except Exception:  # noqa: BLE001 — never break the audit; caller falls back to auto_checks
        return None
