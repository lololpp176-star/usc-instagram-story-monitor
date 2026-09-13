# Instagram Stories → Discord (Free Cloud Version)

This project runs in GitHub Actions. Your computer does not need to stay on.

## GitHub Secrets required

- `DISCORD_WEBHOOK_URL`
- `APIFY_TOKEN`

## What happens

- GitHub Actions runs once per day.
- The 17 public organization accounts are checked in 4 groups.
- Apify returns currently active Stories.
- New Story images/videos are sent to Discord.
- `seen_instagram_stories.json` is automatically committed by GitHub Actions so Stories are not reposted.
- The first run creates the baseline and does not post old/current Stories.

## Important free-tier limitation

The chosen Apify Actor currently limits free accounts to 10 Story results per run and 40 Story results per day. This project therefore uses 4 Actor runs per daily check.
