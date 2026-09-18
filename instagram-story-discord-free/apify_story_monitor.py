import json
import mimetypes
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from apify_client import ApifyClient


USERNAMES = [
    "sc.zbt",
    "universityparkifc",
    "phidelt.sc",
    "snu.usc",
    "chiphi.usc",
    "phisigusc",
    "usc.ka",
    "sc.beta",
    "lxa.usc",
    "uscpanhellenic",
    "uscthetaxi",
    "usc.sammy",
    "uscdelts",
    "tke.sc",
    "sc.ato",
    "sigmachi.sc",
    "usckappasig",
    "lacasadeusc",
    "usccasa",
    "uscalphasig",
    "ago.usc",
    "usc.pikapp",
    "sigepusc",
    "uscphipsi",
    "usc.sae",
    "aepiusc",
    "zetapsi.sc",
    "scdeltaeta",
    "uscvalorant",
    "usc.games",
    "uscesports",
    "usc_deadlock",
]

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "seen_instagram_stories_instaloader.json"
ACTOR_ID = "data-slayer/instagram-stories-scraper"
CHECK_INTERVAL_SECONDS = 12 * 60 * 60
RETRY_INTERVAL_SECONDS = 30 * 60

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
CASA_DISCORD_WEBHOOK_URL = os.getenv("CASA_DISCORD_WEBHOOK_URL", "").strip()
MONITOR_ONLY = os.getenv("MONITOR_ONLY", "").strip().lower()
FORCE_CHECK = os.getenv("FORCE_CHECK", "").lower() in {"1", "true", "yes"}
BASELINE_ONLY = os.getenv("BASELINE_ONLY", "").lower() in {"1", "true", "yes"}

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

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "USC-Public-Story-Monitor/3.0"})


@dataclass(frozen=True)
class StoryItem:
    story_id: str
    username: str
    media_url: str
    thumbnail_url: str
    is_video: bool


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        raise RuntimeError(f"Could not read Story state: {exc}") from exc


def save_state(state):
    seen = sorted({str(value) for value in state.get("seen", [])})
    if len(seen) > 10000:
        seen = seen[-10000:]
    state["seen"] = seen
    state["apify_initialized"] = sorted(
        {str(value).lower() for value in state.get("apify_initialized", [])}
    )
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def selected_usernames():
    if not MONITOR_ONLY:
        return USERNAMES
    requested = [value.strip() for value in MONITOR_ONLY.split(",") if value.strip()]
    unknown = [value for value in requested if value not in USERNAMES]
    if unknown:
        raise RuntimeError("Unknown monitored account(s): " + ", ".join(unknown))
    return requested


def first_value(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", []):
            return value
    return None


def nested_first(mapping, paths):
    for path in paths:
        value = mapping
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value not in (None, "", []):
            return value
    return None


def media_candidate(record, video):
    if video:
        value = first_value(record, ("video_url", "videoUrl", "video"))
        if isinstance(value, str):
            return value
        versions = record.get("video_versions") or record.get("videoVersions") or []
        if versions and isinstance(versions[0], dict):
            return versions[0].get("url", "")

    value = first_value(
        record,
        ("media_url", "mediaUrl", "image_url", "imageUrl", "display_url", "displayUrl", "url"),
    )
    if isinstance(value, str):
        return value
    candidates = nested_first(
        record,
        (("image_versions2", "candidates"), ("imageVersions2", "candidates")),
    ) or []
    if candidates and isinstance(candidates[0], dict):
        return candidates[0].get("url", "")
    return ""


def normalize_record(record, inherited_username=""):
    if not isinstance(record, dict):
        return None

    username = first_value(
        record,
        ("username", "ownerUsername", "owner_username", "sourceUsername", "source_username"),
    ) or nested_first(record, (("user", "username"), ("owner", "username")))
    username = str(username or inherited_username).lower().lstrip("@")

    story_id = first_value(
        record,
        ("story_id", "storyId", "story_pk", "storyPk", "pk", "id", "shortCode", "shortcode"),
    )
    if isinstance(story_id, dict):
        story_id = None

    media_type = first_value(record, ("media_type", "mediaType", "type"))
    video_url = media_candidate(record, True)
    is_video = bool(video_url) or str(media_type).lower() in {"2", "video", "reel"}
    media_url = video_url or media_candidate(record, False)
    thumbnail_url = first_value(
        record,
        ("thumbnail_url", "thumbnailUrl", "display_url", "displayUrl", "image_url", "imageUrl"),
    )
    if not isinstance(thumbnail_url, str):
        thumbnail_url = media_candidate(record, False)

    if not username or username not in USERNAMES or not story_id or not media_url:
        return None
    return StoryItem(
        story_id=str(story_id),
        username=username,
        media_url=str(media_url),
        thumbnail_url=str(thumbnail_url or ""),
        is_video=is_video,
    )


def normalize_results(records):
    stories = []
    for record in records:
        if not isinstance(record, dict):
            continue
        inherited = str(
            first_value(
                record,
                ("username", "ownerUsername", "owner_username", "sourceUsername", "source_username"),
            )
            or ""
        ).lower()
        nested = first_value(record, ("stories", "items", "results"))
        if isinstance(nested, list):
            for child in nested:
                item = normalize_record(child, inherited)
                if item:
                    stories.append(item)
            continue
        item = normalize_record(record)
        if item:
            stories.append(item)

    deduplicated = {}
    for item in stories:
        deduplicated[(item.username, item.story_id)] = item
    return list(deduplicated.values())


def fetch_stories(usernames):
    client = ApifyClient(APIFY_TOKEN)
    run = client.actor(ACTOR_ID).call(run_input={"usernames": usernames})
    if not run or not run.get("defaultDatasetId"):
        raise RuntimeError("Apify did not return a dataset for the Story check.")
    records = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    stories = normalize_results(records)
    print(
        f"Apify returned {len(records)} record(s); normalized {len(stories)} Story/Stories."
    )
    if records and not stories:
        sample_keys = sorted(records[0].keys()) if isinstance(records[0], dict) else []
        raise RuntimeError(
            "The Apify response format was not recognized. First record keys: "
            + ", ".join(sample_keys)
        )
    return stories


def destination_for(username):
    if username in CASA_USERNAMES:
        if not CASA_DISCORD_WEBHOOK_URL:
            raise RuntimeError("CASA_DISCORD_WEBHOOK_URL is missing.")
        return CASA_DISCORD_WEBHOOK_URL, CASA_ROLE_ID
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is missing.")
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
                webhook_url, params={"wait": "true"}, json=payload, timeout=45
            )
        if response.status_code == 429:
            try:
                delay = float(response.json().get("retry_after", 1.5))
            except Exception:
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
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
    return guessed if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"


def post_story(item):
    profile_url = f"https://www.instagram.com/{item.username}/"
    webhook_url, role_id = destination_for(item.username)
    content = f"||<@&{role_id}>||\n\u200b"
    embed = {
        "description": f"### **[@{item.username}]({profile_url}) — New Instagram Story**",
        "footer": {"text": "Made by @minirml"},
    }
    payload = {
        "content": content,
        "embeds": [embed],
        "allowed_mentions": {"parse": [], "roles": [role_id]},
    }

    try:
        media_bytes, content_type = download_media(item.media_url)
        extension = extension_for(item.is_video, content_type)
        filename = f"{item.username.replace('.', '_')}_{item.story_id}{extension}"
        if not item.is_video:
            embed["image"] = {"url": f"attachment://{filename}"}
        elif item.thumbnail_url:
            embed["image"] = {"url": item.thumbnail_url}
        files = {
            "files[0]": (
                filename,
                media_bytes,
                content_type or ("video/mp4" if item.is_video else "application/octet-stream"),
            )
        }
        if discord_request(webhook_url, payload, files=files):
            return True
    except Exception as exc:
        print(f"Could not upload media for @{item.username}: {exc}")

    embed["description"] += f"\n[Open media]({item.media_url})"
    return discord_request(webhook_url, payload)


def main():
    if not APIFY_TOKEN:
        print("APIFY_TOKEN is not configured; Story check safely skipped.")
        return

    state = load_state()
    now = int(time.time())
    last_check = int(state.get("apify_last_check", 0) or 0)
    last_attempt = int(state.get("apify_last_attempt", 0) or 0)
    if not FORCE_CHECK and now - last_check < CHECK_INTERVAL_SECONDS:
        remaining = CHECK_INTERVAL_SECONDS - (now - last_check)
        print(f"Next Apify Story check is due in about {remaining // 60} minute(s).")
        return
    if not FORCE_CHECK and now - last_attempt < RETRY_INTERVAL_SECONDS:
        remaining = RETRY_INTERVAL_SECONDS - (now - last_attempt)
        print(f"Waiting about {remaining // 60} minute(s) before retrying Apify.")
        return

    state["apify_last_attempt"] = now
    save_state(state)
    usernames = selected_usernames()
    stories = fetch_stories(usernames)
    seen = {str(value) for value in state.get("seen", [])}
    initialized = {
        str(value).lower() for value in state.get("apify_initialized", [])
    }

    by_username = {username: [] for username in usernames}
    for item in stories:
        if item.username in by_username:
            by_username[item.username].append(item)

    posted = 0
    for username in usernames:
        current = by_username[username]
        current_ids = {item.story_id for item in current}
        if BASELINE_ONLY or username not in initialized:
            seen.update(current_ids)
            initialized.add(username)
            print(f"Baseline saved for @{username}; nothing posted.")
            continue
        for item in current:
            if item.story_id in seen:
                continue
            if post_story(item):
                seen.add(item.story_id)
                save_state(state | {"seen": list(seen), "apify_initialized": list(initialized)})
                posted += 1
                print(f"Posted @{username} Story {item.story_id}.")
                time.sleep(1)
            else:
                raise RuntimeError(f"Discord post failed for @{username} Story {item.story_id}.")

    state["seen"] = list(seen)
    state["apify_initialized"] = list(initialized)
    state["apify_last_check"] = now
    save_state(state)
    print(f"Finished. Posted {posted} new Story/Stories.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Monitor failed: {exc}", file=sys.stderr)
        raise
