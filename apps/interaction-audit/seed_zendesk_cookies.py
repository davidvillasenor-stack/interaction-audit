#!/usr/bin/env python3
"""One-time seed of ~/.zendesk-mcp-auth.json from your logged-in Chrome session.

Run this yourself (it needs Keychain approval, which can't be granted headlessly):
    python3 ~/batting-cage/apps/interaction-audit/seed_zendesk_cookies.py

When macOS pops "Python wants to use confidential information stored in Chrome Safe
Storage" → click **Always Allow**. It finds the right Chrome profile automatically,
extracts opendoor.zendesk.com cookies, validates them against the live API, and writes
the auth file the audit tool reads. No MFA, no browser window.
"""
import glob, json, os, sys, time
import requests

ZENDESK_URL = "https://opendoor.zendesk.com"
AUTH_FILE = os.path.expanduser("~/.zendesk-mcp-auth.json")
CHROME = os.path.expanduser("~/Library/Application Support/Google/Chrome")

try:
    from pycookiecheat import chrome_cookies
except ImportError:
    sys.exit("pycookiecheat not installed — run: pip3 install pycookiecheat")

# Chrome moved cookies to Profile*/Network/Cookies on newer builds; older keep Profile*/Cookies.
# David's profiles are "Profile 1"/"Profile 3" (not "Default"), so search all of them.
candidates = []
for prof in sorted(glob.glob(f"{CHROME}/Default") + glob.glob(f"{CHROME}/Profile *") + glob.glob(f"{CHROME}/Guest Profile")):
    for sub in ("Network/Cookies", "Cookies"):
        f = os.path.join(prof, sub)
        if os.path.isfile(f):
            candidates.append(f)
if not candidates:
    sys.exit(f"No Chrome cookie DB found under {CHROME}")

print("Chrome cookie DBs found:")
for c in candidates:
    print("  •", c.replace(os.path.expanduser("~"), "~"))

def valid(cookies):
    if not cookies or "_zendesk_session" not in cookies:
        return None
    try:
        r = requests.get(f"{ZENDESK_URL}/api/v2/users/me.json", cookies=cookies, timeout=15)
        if r.status_code != 200:
            return None
        u = r.json().get("user", {})
        name, role = (u.get("name") or ""), (u.get("role") or "")
        if not name or "anonymous" in name.lower() or role == "end-user":
            return None
        return name
    except Exception:
        return None

chosen = None
for cf in candidates:
    try:
        cookies = chrome_cookies(ZENDESK_URL, cookie_file=cf) or {}
    except Exception as e:
        print(f"  ✗ {os.path.basename(os.path.dirname(cf))}: {str(e)[:70]}")
        continue
    name = valid(cookies)
    prof = cf.replace(CHROME + "/", "").split("/")[0]
    print(f"  → {prof}: {len(cookies)} cookies, _zendesk_session={'_zendesk_session' in cookies}, "
          f"{'VALID as ' + name if name else 'not a live session'}")
    if name:
        chosen = cookies
        break

if not chosen:
    sys.exit("No profile had a live opendoor.zendesk.com session. Log into Zendesk in Chrome, then re-run.")

json.dump({"mode": "cookie", "cookies": chosen, "csrf_token": "",
           "refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "pycookiecheat-seed"},
          open(AUTH_FILE, "w"), indent=2)
os.chmod(AUTH_FILE, 0o600)
print(f"✓ wrote {AUTH_FILE} — the audit tool will now pull live Zendesk emails.")
