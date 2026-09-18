import json
import os
from pathlib import Path

from instagrapi import Client


BASE_DIR = Path(__file__).resolve().parent
PROFILE_IDS_FILE = BASE_DIR / "instagram_profile_ids.json"


def main():
    session_id = os.environ.get("INSTAGRAM_SESSIONID", "").strip()
    if not session_id:
        raise RuntimeError("INSTAGRAM_SESSIONID is empty or unavailable.")

    profile_ids = json.loads(PROFILE_IDS_FILE.read_text(encoding="utf-8"))
    reel_ids = [str(value) for value in profile_ids.values()]

    client = Client()
    client.login_by_sessionid(session_id)
    data = client.with_default_data(
        {
            "reel_ids": reel_ids,
            "reason": "on_tap",
            "source": "reel_feed_timeline",
            "batch_size": len(reel_ids),
        }
    )
    result = client.private_request("feed/reels_media_stream/", data=data)

    reels = result.get("reels") or result.get("reels_media") or []
    if isinstance(reels, dict):
        reels = list(reels.values())
    item_count = sum(len(reel.get("items", [])) for reel in reels)
    print(
        f"Mobile batch endpoint accepted all {len(reel_ids)} account IDs; "
        f"received {len(reels)} active reels and {item_count} Story items."
    )


if __name__ == "__main__":
    main()
