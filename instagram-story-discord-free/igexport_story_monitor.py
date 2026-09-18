import json
import mimetypes
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


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
PROVIDER_URL = "https://igexport.com/en/ig-story-download/"
COHORT_COUNT = 6

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
CASA_DISCORD_WEBHOOK_URL = os.getenv("CASA_DISCORD_WEBHOOK_URL", "").strip()
MONITOR_ONLY = os.getenv("MONITOR_ONLY", "").strip().lower()
BASELINE_ONLY = os.getenv("BASELINE_ONLY", "").lower() in {"1", "true", "yes"}

DISCORD_PING_USER_ID = "1548822200545579098"
CASA_ROLE_ID = "1548865195634327613"

HTTP = requests.Session()
HTTP.headers.update(
    {
        "User-Agent": "USC-Public-Story-Monitor/2.0",
        "Accept": "text/html,application/xhtml+xml",
    }
)


def make_browser():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--window-size=1280,900")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(45)
    return driver


@dataclass(frozen=True)
class StoryItem:
    story_id: str
    username: str
    viewer_url: str
    thumbnail_url: str
    is_video: bool


def require_config():
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is missing.")


def load_state():
    if not STATE_FILE.exists():
        return set(), set()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        seen = {str(value) for value in data.get("seen", [])}
        initialized = {
            str(value).lower() for value in data.get("igexport_initialized", [])
        }
        return seen, initialized
    except Exception as exc:
        raise RuntimeError(f"Could not read Story state: {exc}") from exc


def save_state(seen, initialized):
    ids = sorted(seen)
    if len(ids) > 10000:
        ids = ids[-10000:]
    STATE_FILE.write_text(
        json.dumps(
            {
                "seen": ids,
                "igexport_initialized": sorted(initialized),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def selected_usernames():
    if MONITOR_ONLY:
        requested = [value.strip() for value in MONITOR_ONLY.split(",") if value.strip()]
        unknown = [value for value in requested if value not in USERNAMES]
        if unknown:
            raise RuntimeError("Unknown monitored account(s): " + ", ".join(unknown))
        return requested

    cohort = int(time.time() // 300) % COHORT_COUNT
    selected = USERNAMES[cohort::COHORT_COUNT]
    print(f"Checking cohort {cohort + 1}/{COHORT_COUNT}: " + ", ".join("@" + u for u in selected))
    return selected


def wait_for_profile_result(driver, username):
    wanted = username.lower()

    def result_ready(current_driver):
        headings = current_driver.find_elements(By.TAG_NAME, "h2")
        if any(heading.text.strip().lower() == wanted for heading in headings):
            return True
        body = current_driver.find_element(By.TAG_NAME, "body").text.lower()
        terminal_markers = (
            "profile not found",
            "account not found",
            "an error occurred",
            "try again later",
        )
        return any(marker in body for marker in terminal_markers)

    WebDriverWait(driver, 35).until(result_ready)


def fetch_stories(driver, username):
    url = f"{PROVIDER_URL}?username={quote(username)}"
    driver.get(url)
    try:
        wait_for_profile_result(driver, username)
    except TimeoutException as exc:
        raise RuntimeError(f"Public viewer timed out for @{username}.") from exc

    soup = BeautifulSoup(driver.page_source, "html.parser")

    profile_found = any(
        heading.get_text(" ", strip=True).lower() == username
        for heading in soup.find_all("h2")
    )
    if not profile_found:
        raise RuntimeError(f"IGExport did not return a profile result for @{username}.")

    stories = []
    found_ids = set()
    for link in soup.select('a[href*="story="]'):
        href = link.get("href", "")
        story_id = parse_qs(urlparse(href).query).get("story", [""])[0]
        if not story_id.isdigit() or story_id in found_ids:
            continue

        image = link.find("img")
        thumbnail_url = image.get("src", "") if image else ""
        text = link.get_text(" ", strip=True).lower()
        stories.append(
            StoryItem(
                story_id=story_id,
                username=username,
                viewer_url=urljoin(PROVIDER_URL, href),
                thumbnail_url=thumbnail_url,
                is_video="video" in text,
            )
        )
        found_ids.add(story_id)

    print(f"@{username}: {len(stories)} active Story/Stories.")
    return stories


def resolve_media(driver, item):
    driver.get(item.viewer_url)
    try:
        WebDriverWait(driver, 30).until(
            lambda current_driver: current_driver.find_elements(
                By.CSS_SELECTOR, "main video[src], main img[src]"
            )
        )
    except TimeoutException:
        pass
    soup = BeautifulSoup(driver.page_source, "html.parser")

    video = soup.select_one("main video[src]")
    if video and video.get("src"):
        return video["src"], True

    for image in soup.select("main img[src]"):
        classes = set(image.get("class", []))
        if {"w-full", "h-full", "object-contain"}.issubset(classes):
            return image["src"], False

    if item.thumbnail_url:
        return item.thumbnail_url, item.is_video
    raise RuntimeError(f"No media URL found for Story {item.story_id}.")


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
    if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return guessed
    return ".jpg"


def destination_for(username):
    if username in {
        "lacasadeusc",
        "usccasa",
        "uscesports",
        "usc.games",
        "uscvalorant",
        "usc_deadlock",
    }:
        if not CASA_DISCORD_WEBHOOK_URL:
            raise RuntimeError("CASA_DISCORD_WEBHOOK_URL is missing.")
        return CASA_DISCORD_WEBHOOK_URL, CASA_ROLE_ID
    return DISCORD_WEBHOOK_URL, DISCORD_PING_USER_ID


def post_story(driver, item):
    media_url, is_video = resolve_media(driver, item)
    profile_url = f"https://www.instagram.com/{item.username}/"
    webhook_url, role_id = destination_for(item.username)
    message_content = f"||<@&{role_id}>||\n\u200b"

    embed = {
        "description": f"### **[@{item.username}]({profile_url}) — New Instagram Story**",
        "footer": {"text": "Made by @minirml"},
    }
    payload = {
        "content": message_content,
        "embeds": [embed],
        "allowed_mentions": {"parse": [], "roles": [role_id]},
    }

    try:
        media_bytes, content_type = download_media(media_url)
        extension = extension_for(is_video, content_type)
        filename = f"{item.username.replace('.', '_')}_{item.story_id}{extension}"
        if not is_video:
            embed["image"] = {"url": f"attachment://{filename}"}
        elif item.thumbnail_url:
            embed["image"] = {"url": item.thumbnail_url}

        files = {
            "files[0]": (
                filename,
                media_bytes,
                content_type or ("video/mp4" if is_video else "application/octet-stream"),
            )
        }
        if discord_request(webhook_url, payload, files=files):
            return True
        print(f"Discord media upload failed for @{item.username}; sending viewer link.")
    except Exception as exc:
        print(f"Could not upload media for @{item.username}: {exc}")

    fallback = {
        "content": message_content,
        "embeds": [
            {
                "description": (
                    f"### **[@{item.username}]({profile_url}) — New Instagram Story**\n"
                    f"[Open Story]({item.viewer_url})"
                ),
                "footer": {"text": "Made by @minirml"},
            }
        ],
        "allowed_mentions": {"parse": [], "roles": [role_id]},
    }
    return discord_request(webhook_url, fallback)


def main():
    require_config()
    seen, initialized = load_state()
    posted = 0
    errors = []

    selected = selected_usernames()
    successful_accounts = 0
    driver = make_browser()
    try:
        for username in selected:
            try:
                stories = fetch_stories(driver, username)
                successful_accounts += 1
                current_ids = {item.story_id for item in stories}

                if username not in initialized or BASELINE_ONLY:
                    seen.update(current_ids)
                    initialized.add(username)
                    save_state(seen, initialized)
                    print(f"Baseline saved for @{username}; nothing posted.")
                    continue

                for item in stories:
                    if item.story_id in seen:
                        continue
                    if post_story(driver, item):
                        seen.add(item.story_id)
                        save_state(seen, initialized)
                        posted += 1
                        print(f"Posted @{username} Story {item.story_id}.")
                        time.sleep(1)
                    else:
                        errors.append(
                            f"Discord post failed for @{username} Story {item.story_id}"
                        )
            except Exception as exc:
                errors.append(f"@{username}: {exc}")
            time.sleep(1)
    finally:
        driver.quit()

    save_state(seen, initialized)
    print(f"Finished. Posted {posted} new Story/Stories.")
    if errors:
        print("Warnings: " + "; ".join(errors), file=sys.stderr)
    if successful_accounts == 0:
        raise RuntimeError("; ".join(errors))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Monitor failed: {exc}", file=sys.stderr)
        raise
