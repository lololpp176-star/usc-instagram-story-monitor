import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import instaloader
import requests

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
]

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "seen_instagram_stories_instaloader.json"
PROFILE_IDS_FILE = BASE_DIR / "instagram_profile_ids.json"

IG_USERNAME = os.getenv("IG_USERNAME", "botwatch92848").strip()
SESSION_FILE = os.getenv("IG_SESSION_FILE", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "").strip()
TEST_DISCORD_ONLY = os.getenv("TEST_DISCORD_ONLY", "").lower() in {"1", "true", "yes"}

# Discord user to ping before each Story embed.
DISCORD_PING_USER_ID = "1548822200545579098"

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "USC-Instagram-Story-Monitor/1.0"})


def require_config():
    missing = []
    if not IG_USERNAME:
        missing.append("IG_USERNAME")
    if not SESSION_FILE:
        missing.append("IG_SESSION_FILE")
    if not DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")
    if missing:
        print("Missing configuration: " + ", ".join(missing))
        sys.exit(2)


def load_seen():
    if not STATE_FILE.exists():
        return set(), True
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(str(x) for x in data.get("seen", [])), False
    except Exception:
        return set(), True


def save_seen(seen):
    ids = sorted(seen)
    if len(ids) > 10000:
        ids = ids[-10000:]
    STATE_FILE.write_text(
        json.dumps({"seen": ids}, indent=2) + "\n",
        encoding="utf-8",
    )


def make_loader():
    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        max_connection_attempts=2,
        request_timeout=60.0,
        quiet=True,
    )
    loader.load_session_from_file(IG_USERNAME, filename=SESSION_FILE)
    logged_in_as = loader.test_login()
    if not logged_in_as:
        raise RuntimeError(
            "The Instagram session is no longer valid. "
            "Create a fresh Instaloader session and replace the GitHub secret."
        )
    print(f"Instagram session valid for @{logged_in_as}.")
    return loader


def load_or_resolve_profile_ids(loader):
    existing = {}
    if PROFILE_IDS_FILE.exists():
        try:
            existing = json.loads(PROFILE_IDS_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    ids = {
        username: int(existing[username])
        for username in USERNAMES
        if username in existing and str(existing[username]).isdigit()
    }

    missing = [u for u in USERNAMES if u not in ids]
    if not missing:
        print("Loaded all Instagram numeric profile IDs from repository state.")
        return ids

    if not APIFY_TOKEN:
        raise RuntimeError(
            "Instagram numeric profile IDs are missing and APIFY_TOKEN is not available."
        )

    print(
        f"Resolving {len(missing)} Instagram numeric IDs through "
        "Apify's official profile scraper..."
    )

    actor_url = (
        "https://api.apify.com/v2/actors/"
        "apify~instagram-profile-scraper/run-sync-get-dataset-items"
    )

    # Small batches avoid one long-running Actor call timing out.
    batch_size = 6
    for offset in range(0, len(missing), batch_size):
        batch = missing[offset:offset + batch_size]
        print(
            f"Resolving batch {offset // batch_size + 1}: "
            + ", ".join("@" + u for u in batch)
        )

        response = HTTP.post(
            actor_url,
            headers={
                "Authorization": f"Bearer {APIFY_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "usernames": batch,
                "includeAboutSection": False,
            },
            timeout=300,
        )
        response.raise_for_status()

        rows = response.json()
        if not isinstance(rows, list):
            raise RuntimeError("Unexpected Apify profile response.")

        for row in rows:
            username = str(row.get("username") or "").lower()
            user_id = str(row.get("id") or row.get("userId") or "")
            if username in USERNAMES and user_id.isdigit():
                ids[username] = int(user_id)

        PROFILE_IDS_FILE.write_text(
            json.dumps(ids, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    still_missing = [u for u in USERNAMES if u not in ids]
    if still_missing:
        raise RuntimeError(
            "Could not resolve these Instagram IDs: " + ", ".join(still_missing)
        )

    print("Saved all Instagram numeric profile IDs.")
    return ids


def discord_request(payload, files=None):
    for _ in range(5):
        if files:
            response = HTTP.post(
                DISCORD_WEBHOOK_URL,
                params={"wait": "true"},
                data={"payload_json": json.dumps(payload)},
                files=files,
                timeout=90,
            )
        else:
            response = HTTP.post(
                DISCORD_WEBHOOK_URL,
                params={"wait": "true"},
                json=payload,
                timeout=45,
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
    return (
        response.content,
        response.headers.get("Content-Type", "application/octet-stream"),
    )


def extension_for(is_video, content_type):
    if is_video:
        return ".mp4"
    guessed = mimetypes.guess_extension(
        (content_type or "").split(";")[0].strip()
    )
    if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return guessed
    return ".jpg"


def post_story_item(item, username):
    story_id = str(item.mediaid)
    media_url = item.video_url if item.is_video else item.url
    profile_url = f"https://www.instagram.com/{username}/"

    # Spoilered role ping, then a linked bold Story title, followed by a
    # dedicated spacer line before the embed.
    message_content = (
        f"||<@&{DISCORD_PING_USER_ID}>||\n\n"
        f"**[@{username} — New Instagram Story]({profile_url})**\n\n"
        "\u200b"
    )

    embed = {
        "timestamp": item.date_utc.isoformat(),
        "footer": {
            "text": "USC Instagram Story monitor • Made by @minirrml"
        },
    }

    try:
        media_bytes, content_type = download_media(media_url)
        ext = extension_for(item.is_video, content_type)
        filename = f"{username.replace('.', '_')}_{story_id}{ext}"

        if not item.is_video:
            # Use Discord's full-size embed image slot. Discord itself controls
            # the final on-screen size for portrait media.
            embed["image"] = {"url": f"attachment://{filename}"}
        else:
            try:
                embed["image"] = {"url": item.url}
            except Exception:
                pass

        payload = {
            "content": message_content,
            "embeds": [embed],
            "allowed_mentions": {
                "parse": [],
                "roles": [DISCORD_PING_USER_ID],
            },
        }
        files = {
            "files[0]": (
                filename,
                media_bytes,
                content_type or "application/octet-stream",
            )
        }

        if discord_request(payload, files=files):
            return True

        print(f"Discord upload failed for @{username}; trying direct media URL.")

    except Exception as exc:
        print(f"Could not download media for @{username}: {exc}")

    fallback_embed = {
        **embed,
    }

    if not item.is_video:
        fallback_embed["image"] = {"url": media_url}

    fallback = {
        "content": message_content,
        "embeds": [fallback_embed],
        "allowed_mentions": {
            "parse": [],
            "roles": [DISCORD_PING_USER_ID],
        },
    }

    if item.is_video:
        fallback["content"] += f"\n{media_url}"

    return discord_request(fallback)


def send_discord_test():
    require_config()

    loader = make_loader()
    profile_ids = load_or_resolve_profile_ids(loader)
    id_to_username = {v: k for k, v in profile_ids.items()}

    recent = []
    for story in loader.get_stories(userids=list(profile_ids.values())):
        username = id_to_username.get(int(story.owner_id), story.owner_username)
        for item in story.get_items():
            recent.append((item.date_utc, item, username))

    if not recent:
        raise RuntimeError(
            "None of the monitored accounts currently has an active Story to use for testing."
        )

    recent.sort(key=lambda x: x[0], reverse=True)
    _, item, username = recent[0]

    print(
        f"Testing Discord format with the most recent active Story from @{username} "
        f"(Story {item.mediaid})."
    )

    if not post_story_item(item, username):
        raise RuntimeError("Discord live Story test failed.")

    print("Live Story embed test sent successfully.")


def main():
    if TEST_DISCORD_ONLY:
        send_discord_test()
        return

    require_config()
    seen, first_run = load_seen()

    loader = make_loader()
    profile_ids = load_or_resolve_profile_ids(loader)
    wanted_ids = set(profile_ids.values())
    id_to_username = {v: k for k, v in profile_ids.items()}

    found_items = []

    for story in loader.get_stories(userids=list(wanted_ids)):
        username = id_to_username.get(int(story.owner_id), story.owner_username)
        for item in story.get_items():
            found_items.append((item.date_utc, item, username))

    found_items.sort(key=lambda x: x[0])

    current_ids = {str(item.mediaid) for _, item, _ in found_items}

    if first_run:
        seen.update(current_ids)
        save_seen(seen)
        print(
            f"Initial baseline saved ({len(current_ids)} active Stories). "
            "Nothing posted."
        )
        return

    posted = 0
    for _, item, username in found_items:
        story_id = str(item.mediaid)
        if story_id in seen:
            continue

        if post_story_item(item, username):
            seen.add(story_id)
            save_seen(seen)
            posted += 1
            print(f"Posted @{username} Story {story_id}.")
            time.sleep(1)

    save_seen(seen)
    print(f"Finished. Posted {posted} new Story/Stories.")


if __name__ == "__main__":
    main()
