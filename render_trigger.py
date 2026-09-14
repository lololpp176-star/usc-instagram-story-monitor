import json
import os
import sys
import urllib.error
import urllib.request

TOKEN = os.getenv("GH_WORKFLOW_TOKEN", "").strip()
if not TOKEN:
    print("GH_WORKFLOW_TOKEN is not set. Add it in Render Environment.")
    sys.exit(2)

url = (
    "https://api.github.com/repos/"
    "lololpp176-star/usc-instagram-story-monitor/"
    "actions/workflows/instagram-stories-instaloader.yml/dispatches"
)

payload = json.dumps({
    "ref": "main",
    "inputs": {"mode": "normal"},
}).encode("utf-8")

request = urllib.request.Request(
    url,
    data=payload,
    method="POST",
    headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "usc-instagram-story-monitor-render-trigger",
        "Content-Type": "application/json",
    },
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        print(f"GitHub workflow dispatch accepted: HTTP {response.status}")
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace")
    print(f"GitHub workflow dispatch failed: HTTP {exc.code}: {body[:1000]}")
    sys.exit(1)
except Exception as exc:
    print(f"GitHub workflow dispatch failed: {exc}")
    sys.exit(1)
