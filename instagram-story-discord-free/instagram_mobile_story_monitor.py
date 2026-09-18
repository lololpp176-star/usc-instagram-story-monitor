import json
import mimetypes
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from instagrapi import Client
from instagrapi.exceptions import ClientJSONDecodeError


BASE_DIR = Path(__file__).resolve().parent
PROFILE_IDS_FILE = BASE_DIR / "instagram_profile_ids.json"
STATE_FILE = BASE_DIR / "seen_instagram_stories_instaloader.json"

DEFAULT_ROLE_ID = "1548822200545579098"
CASA_ROLE_ID = "1548865195634327613"
CASA_USERNAMES = {
    "lacasadeusc",
    "usccasa",
    "uscesports",
    "usc.games",
    "uscvalorant",
    "usc_deadlock",
}

SESSION_ID = os.environ.get("INSTAGRAM_SESSIONID", "").strip()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
CASA_DISCORD_WEBHOOK_URL = os.environ.get("CASA_DISCORD_WEBHOOK_URL", "").strip()
BASELINE_ONLY = os.environ.get("BASELINE_ONLY", "").lower() in {"1", "true", "yes"}

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "USC-Instagram-Story-Monitor/2.0"})


def require_config():
    missing = []
    if not SESSION_ID:
        missing.append("INSTAGRAM_SESSIONID")
    if not DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")
    if not CASA_DISCORD_WEBHOOK_URL:
        missing.append("CASA_DISCORD_WEBHOOK_URL")
    if missing:
        raise RuntimeError("Missing configuration: " + ", ".join(missing))


def load_seen():
    if not STATE_FILE.exists():
        return set(), True
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {str(value) for value in payload.get("seen", [])}, False
    except (OSError, ValueError, TypeError):
        return set(), True


def save_seen(seen):
    ids = sorted(seen)
    if len(ids) > 10000:
        ids = ids[-10000:]
    STATE_FILE.write_text(
        json.dumps({"seen": ids}, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_streamed_response(response_text):
    remaining = response_text.lstrip()
    decoder = json.JSONDecoder()
    objects = []
    while remaining:
        value, offset = decoder.raw_decode(remaining)
        objects.append(value)
        remaining = remaining[offset:].lstrip()
    result = next(
        (
            value
            for value in objects
            if isinstance(value, dict)
            and (value.get("reels") is not None or value.get("reels_media") is not None)
        ),
        None,
    )
    if result is None:
        raise RuntimeError("Instagram returned no Story payload.")
    print(f"Accepted streamed Instagram response ({len(objects)} JSON objects).")
    return result


def fetch_reels(profile_ids):
    client = Client()
    client.login_by_sessionid(SESSION_ID)

    reel_ids = [str(value) for value in profile_ids.values()]
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
        result = parse_streamed_response(client.last_response.text)

    reels = result.get("reels") or result.get("reels_media") or {}
    if isinstance(reels, dict):
        return list(reels.values())
    if isinstance(reels, list):
        return reels
    raise RuntimeError("Instagram returned an unexpected Story payload.")


def story_id(item):
    value = str(item.get("pk") or item.get("id") or "")
    # Mobile media IDs can be formatted as "story_pk_owner_id".
    return value.split("_", 1)[0]


def best_candidate(candidates):
    usable = [entry for entry in (candidates or []) if entry.get("url")]
    if not usable:
        return ""
    return max(
        usable,
        key=lambda entry: int(entry.get("width") or 0) * int(entry.get("height") or 0),
    )["url"]


def image_url(item):
    return best_candidate((item.get("image_versions2") or {}).get("candidates"))


def video_url(item):
    return best_candidate(item.get("video_versions"))


def story_timestamp(item):
    value = item.get("taken_at") or item.get("device_timestamp")
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1_000_000
        return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc).isoformat()


def destination_for(username):
    if username in CASA_USERNAMES:
        return CASA_DISCORD_WEBHOOK_URL, CASA_ROLE_ID
    return DISCORD_WEBHOOK_URL, DEFAULT_ROLE_ID


def discord_request(webhook_url, payload, files=None):
    for _ in range(5):
        if files:
            response = HTTP.post(
                webhook_url,
                params={"wait": "true"},
                data={"payload_json": json.dumps(payload)},
                files=files,
                timeout=90,
            )
        else:
            response = HTTP.post(
                webhook_url,
                params={"wait": "true"},
                json=payload,
                timeout=45,
            )

        if response.status_code == 429:
            try:
                delay = float(response.json().get("retry_after", 1.5))
            except (ValueError, TypeError):
                delay = 1.5
            time.sleep(max(delay, 0.5))
            continue
        if 200 <= response.status_code < 300:
            return True
        print(f"Discord returned {response.status_code}: {response.text[:500]}")
        return False
    return False


def download_media(url):
    response = HTTP.get(url, timeout=90)
    response.raise_for_status()
    return response.content, response.headers.get("Content-Type", "application/octet-stream")


def extension_for(is_video, content_type):
    if is_video:
        return ".mp4"
    guessed = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip())
    return guessed if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"


def post_story(item, username):
    item_id = story_id(item)
    preview_url = image_url(item)
    movie_url = video_url(item)
    is_video = int(item.get("media_type") or 0) == 2 or bool(movie_url)
    media_url = movie_url if is_video else preview_url
    if not media_url:
        print(f"Story {item_id} from @{username} has no downloadable media URL.")
        return False

    webhook_url, role_id = destination_for(username)
    profile_url = f"https://www.instagram.com/{username}/"
    message_content = f"||<@&{role_id}>||\n\u200b"
    embed = {
        "description": f"### **[@{username}]({profile_url}) — New Instagram Story**",
        "footer": {"text": "Made by @minirml"},
        "timestamp": story_timestamp(item),
    }

    try:
        media_bytes, content_type = download_media(media_url)
        filename = (
            f"{username.replace('.', '_')}_{item_id}"
            f"{extension_for(is_video, content_type)}"
        )
        if is_video:
            if preview_url:
                embed["image"] = {"url": preview_url}
        else:
            embed["image"] = {"url": f"attachment://{filename}"}

        payload = {
            "content": message_content,
            "embeds": [embed],
            "allowed_mentions": {"parse": [], "roles": [role_id]},
        }
        files = {"files[0]": (filename, media_bytes, content_type)}
        if discord_request(webhook_url, payload, files=files):
            return True
        print(f"Discord media upload failed for @{username}; trying a direct media URL.")
    except requests.RequestException as exc:
        print(f"Could not download Story {item_id} from @{username}: {exc}")

    fallback = {
        "content": message_content,
        "embeds": [
            {
                **embed,
                "description": embed["description"] + f"\n{media_url}",
            }
        ],
        "allowed_mentions": {"parse": [], "roles": [role_id]},
    }
    return discord_request(webhook_url, fallback)


def main():
    require_config()
    profile_ids = json.loads(PROFILE_IDS_FILE.read_text(encoding="utf-8"))
    id_to_username = {str(value): username for username, value in profile_ids.items()}
    reels = fetch_reels(profile_ids)

    stories = []
    for reel in reels:
        owner = reel.get("user") or {}
        owner_id = str(reel.get("id") or owner.get("pk") or owner.get("id") or "")
        username = id_to_username.get(owner_id) or str(owner.get("username") or "").lower()
        if username not in profile_ids:
            print(f"Ignoring an unexpected Story owner: {username or owner_id}.")
            continue
        for item in reel.get("items") or []:
            item_id = story_id(item)
            if item_id:
                stories.append((float(item.get("taken_at") or 0), item_id, item, username))

    stories.sort(key=lambda entry: entry[0])
    seen, first_run = load_seen()
    current_ids = {entry[1] for entry in stories}
    print(
        f"Checked all {len(profile_ids)} accounts in one batch; "
        f"found {len(reels)} active accounts and {len(stories)} Story items."
    )

    if first_run or BASELINE_ONLY:
        seen.update(current_ids)
        save_seen(seen)
        print(f"Baseline saved ({len(current_ids)} active Stories). Nothing posted.")
        return

    posted = 0
    for _, item_id, item, username in stories:
        if item_id in seen:
            continue
        if post_story(item, username):
            seen.add(item_id)
            save_seen(seen)
            posted += 1
            print(f"Posted @{username} Story {item_id}.")
            time.sleep(1)

    save_seen(seen)
    print(f"Finished. Posted {posted} new Story/Stories.")


if __name__ == "__main__":
    main()
