from __future__ import annotations
"""
apps/interaction-audit/build.py — bake analyzed flip data into a self-contained index.html.

Reads a JSON file whose top-level object is the DATA dict the UI expects
(keyed by flip token; each value has: customer, address, experience{}, slack[],
summary, gaps[], misstatements[], events[]), injects it into template.html between
the /*__DATA_START__*/ .. /*__DATA_END__*/ markers, swaps the banner, and writes
index.html — ready to open or push to GitHub Pages.

Usage:
    python3 apps/interaction-audit/build.py data.json
    python3 apps/interaction-audit/build.py data.json --out index.html
"""
import json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"

SAMPLE_BANNER = re.compile(r'<div class="demo-banner">.*?</div>', re.DOTALL)
REAL_BANNER = ('<div class="demo-banner">🔒 <b>REAL DATA — REDACTED.</b> '
               'Names → initials, phone/address/email masked; transcript substance kept intact. '
               'For internal fact-checking. Calls & texts from Snowflake; email (Zendesk) coming in Phase 2.</div>')

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage: build.py <data.json> [--out index.html] [--keep-banner]")
        sys.exit(1)
    data = json.loads(Path(args[0]).read_text())
    out = HERE / (sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "index.html")

    html = TEMPLATE.read_text()
    start, end = "/*__DATA_START__*/", "/*__DATA_END__*/"
    i, j = html.index(start), html.index(end) + len(end)
    block = f"{start}\nconst DATA = {json.dumps(data, indent=2, ensure_ascii=False)};\n{end}"
    html = html[:i] + block + html[j:]

    if "--keep-banner" not in sys.argv:
        html = SAMPLE_BANNER.sub(REAL_BANNER, html, count=1)

    out.write_text(html)
    print(f"Built {out}  ({len(data)} flip(s): {', '.join(data.keys())})")

if __name__ == "__main__":
    main()
