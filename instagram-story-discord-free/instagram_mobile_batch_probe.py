import json
import os
from pathlib import Path

from instagrapi import Client
from instagrapi.exceptions import ClientJSONDecodeError


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
    try:
        result = client.private_request("feed/reels_media_stream/", data=data)
    except ClientJSONDecodeError:
        # Instagram can stream a second JSON object after the reels payload.
        # Instagrapi rejects the otherwise-valid response as "Extra data".
        response_text = client.last_response.text.lstrip()
        decoder = json.JSONDecoder()
        objects = []
        while response_text:
            value, offset = decoder.raw_decode(response_text)
            objects.append(value)
            response_text = response_text[offset:].lstrip()
        result = next(
            value for value in objects
            if isinstance(value, dict) and (value.get("reels") or value.get("reels_media"))
        )
        print(f"Accepted streamed Instagram response ({len(objects)} JSON objects).")

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
