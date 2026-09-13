"""
FREE CLOUD VERSION
Instagram Stories -> Discord using GitHub Actions + Apify Free Plan.

No PC needs to stay on.

Environment variables:
  DISCORD_WEBHOOK_URL
  APIFY_TOKEN

The script splits the 17 accounts into four Apify runs because the selected
Story scraper caps free accounts at 10 Story results per run / 40 per day.
"""

import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import requests

ACCOUNT_GROUPS = [
    ["universityparkifc", "sc.zbt", "chiphi.usc", "uscdelts"],
    ["uscpanhellenic", "phidelt.sc", "phisigusc", "tke.sc"],
    ["usc.ka", "sc.beta", "lxa.usc", "sc.ato"],
    ["snu.usc", "uscthetaxi", "usc.sammy", "sigmachi.sc", "usckappasig"],
]

ACTOR_ID = "intropix~instagram-stories-scraper"
APIFY_URL = (
    f"https://api.apify.com/v2/actors/{ACTOR_ID}/"
    "run-sync-get-dataset-items"
)
STATE_FILE = Path(__file__).with_name("seen_instagram_stories.json")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "").strip()
TEST_DISCORD_ONLY = os.getenv("TEST_DISCORD_ONLY", "false").strip().lower() == "true"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "USC-IG-Story-Discord-Bridge/2.0"})


def require_secrets():
    missing = []
    if not DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")
    if not TEST_DISCORD_ONLY and not APIFY_TOKEN:
        missing.append("APIFY_TOKEN")
    if missing:
        print("Missing GitHub secret(s): " + ", ".join(missing))
        sys.exit(2)


def load_state():
    if not STATE_FILE.exists():
        return set(), True
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(str(x) for x in raw.get("seen", [])), False
    except Exception:
        return set(), True


def save_state(seen):
    # A bounded list is plenty for deduplication.
    ids = sorted(seen)
    if len(ids) > 5000:
        ids = ids[-5000:]
    STATE_FILE.write_text(
        json.dumps({"seen": ids}, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_group(usernames):
    payload = {
        "usernames": usernames,
        "maxResults": 10,
    }

    last_error = None
    for attempt in range(3):
        try:
            response = SESSION.post(
                APIFY_URL,
                headers={
                    "Authorization": f"Bearer {APIFY_TOKEN}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
                timeout=300,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise RuntimeError("Apify response was not a JSON list.")
            return data
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                print(
                    f"Apify group failed ({exc}); retrying in "
                    f"{20 * (attempt + 1)} seconds..."
                )
                time.sleep(20 * (attempt + 1))

    print(f"WARNING: Skipping this group after retries: {last_error}")
    return []


def fetch_all_stories():
    combined = []
    for index, group in enumerate(ACCOUNT_GROUPS, start=1):
        print(f"Checking group {index}/{len(ACCOUNT_GROUPS)}: {', '.join(group)}")
        items = fetch_group(group)
        combined.extend(items)

        # Small pause between Actor runs.
        if index < len(ACCOUNT_GROUPS):
            time.sleep(2)

    # Deduplicate in case the upstream source ever returns repeats.
    unique = {}
    for story in combined:
        pk = str(story.get("story_pk") or "")
        if pk:
            unique[pk] = story

    return list(unique.values())


def discord_request(*, payload, files=None):
    for _ in range(5):
        if files:
            response = SESSION.post(
                DISCORD_WEBHOOK_URL,
                params={"wait": "true"},
                data={"payload_json": json.dumps(payload)},
                files=files,
                timeout=90,
            )
        else:
            response = SESSION.post(
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

        print(
            f"Discord returned {response.status_code}: "
            f"{response.text[:500]}"
        )
        return False

    return False


def extension_for(story, content_type):
    if str(story.get("media_type", "")).lower() == "video":
        return ".mp4"

    guessed = mimetypes.guess_extension(
        (content_type or "").split(";")[0].strip()
    )
    if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return guessed
    return ".jpg"


def download_media(url):
    response = SESSION.get(url, timeout=90)
    response.raise_for_status()
    return (
        response.content,
        response.headers.get("Content-Type", "application/octet-stream"),
    )


def build_embed(story, attachment_name=None, remote_image_url=None):
    username = story.get("username") or "instagram"
    full_name = story.get("full_name") or f"@{username}"

    embed = {
        "title": f"New Instagram Story — @{username}",
        "url": f"https://www.instagram.com/{username}/",
        "description": full_name,
        "footer": {"text": "USC Instagram Story monitor"},
    }

    taken_at = story.get("taken_at")
    if taken_at:
        embed["timestamp"] = taken_at

    caption = story.get("caption")
    if caption:
        embed["description"] += f"\n\n{str(caption)[:3000]}"

    if attachment_name:
        embed["image"] = {"url": f"attachment://{attachment_name}"}
    elif remote_image_url:
        embed["image"] = {"url": remote_image_url}

    return embed


def send_story(story):
    username = story.get("username") or "instagram"
    story_id = str(story.get("story_pk") or "")
    media_type = str(story.get("media_type") or "").lower()
    media_url = story.get("media_url")

    if not story_id or not media_url:
        print(f"Skipping malformed Story for @{username}")
        return False

    profile_url = f"https://www.instagram.com/{username}/"
    content = (
        f"**@{username} posted a new Instagram Story**\n"
        f"{profile_url}"
    )

    try:
        media_bytes, content_type = download_media(media_url)
        ext = extension_for(story, content_type)
        filename = f"{username.replace('.', '_')}_{story_id}{ext}"

        if media_type == "image":
            payload = {
                "content": content,
                "embeds": [build_embed(story, attachment_name=filename)],
                "allowed_mentions": {"parse": []},
            }
        else:
            payload = {
                "content": content,
                "embeds": [build_embed(story)],
                "allowed_mentions": {"parse": []},
            }

        files = {
            "files[0]": (
                filename,
                media_bytes,
                content_type or "application/octet-stream",
            )
        }

        if discord_request(payload=payload, files=files):
            print(f"Posted @{username} Story {story_id}")
            return True

        print(
            f"Media upload failed for @{username}; "
            "falling back to the direct Story media URL."
        )

    except Exception as exc:
        print(f"Could not download @{username} Story media: {exc}")

    fallback = {
        "content": f"{content}\n{media_url}",
        "allowed_mentions": {"parse": []},
    }

    if media_type == "image":
        fallback["embeds"] = [
            build_embed(story, remote_image_url=media_url)
        ]

    return discord_request(payload=fallback)


def main():
    require_secrets()

    if TEST_DISCORD_ONLY:
        ok = discord_request(
            payload={
                "content": "✅ Instagram Story monitor test: Discord webhook is working.",
                "allowed_mentions": {"parse": []},
            }
        )
        if not ok:
            sys.exit(1)
        print("Discord webhook test succeeded.")
        return

    seen, first_run = load_state()

    stories = fetch_all_stories()
    stories.sort(key=lambda s: s.get("taken_at") or "")

    current_ids = {
        str(s.get("story_pk"))
        for s in stories
        if s.get("story_pk")
    }

    if first_run:
        # First cloud run establishes a baseline and does NOT flood Discord.
        seen.update(current_ids)
        save_state(seen)
        print(
            f"Initial baseline saved ({len(current_ids)} active Stories). "
            "Nothing posted. New Stories found on later runs will be posted."
        )
        return

    posted = 0

    for story in stories:
        story_id = str(story.get("story_pk") or "")
        if not story_id or story_id in seen:
            continue

        if send_story(story):
            seen.add(story_id)
            save_state(seen)
            posted += 1
            time.sleep(1)

    # Write state even when nothing was posted, keeping formatting consistent.
    save_state(seen)
    print(f"Finished. Posted {posted} new Story/Stories.")


if __name__ == "__main__":
    main()
