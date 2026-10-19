# ZC Watermark Bot for Telegram

A Telegram bot for **text-watermarking videos and PDFs**. It does not accept photo/image files for processing and has no logo/image-watermark feature. Each user's text, font and output settings are stored separately.

- **Videos:** text, font, colour, opacity, background box, outline, shadow, position/motion, CRF, preset, resolution, H.264/H.265, audio and container options.
- **PDFs:** the same text design, page selection, over/under placement, flattening and metadata options.
- **Progress:** download, watermark and upload stages with transfer speed and ETA; video encoding also reports FPS/realtime speed and ETA.
- **Queue:** one shared, sequential watermark-processing queue for videos and PDFs; Infinity jobs receive priority. Transfers remain resource-bounded.
- **Thumbnails:** a Telegram video's original cover thumbnail is retained after processing when Telegram supplies one. Direct-link videos use an unwatermarked source frame.

---

## Plans

Upgrades and renewals are **manual** through `@ZCYT_2026`; no online payments are processed by the bot.

| Plan | Price | Batch limit | File limit | Jobs/day | CRF limit | Priority |
|---|---:|---:|---:|---:|---:|---|
| Free | ₹0 | 5 | 50 MB | 5 | 23 | Standard |
| Plus | ₹149 / 30 days | 50 | 500 MB | 50 | 20 | Standard |
| Pro | ₹299 / 30 days | 200 | 2,000 MB | 200 | 18 | Standard |
| Infinity | ₹499 / 30 days | 500 | 2,000 MB | Unlimited | 16 | Priority |

The referenced screenshot lists Infinity at 4 GB. Telegram bot uploads are limited to **2,000 MB**, so this build enforces 2,000 MB instead of promising an unsupported 4 GB. CRF values lower than a plan's limit are clamped; higher CRFs are still available for smaller output files. See `/plans`, `/myplan` and `/usage` in the bot.

## Quick start

1. Create a bot with [@BotFather](https://t.me/BotFather) and obtain `BOT_TOKEN`.
2. Get `API_ID` and `API_HASH` from <https://my.telegram.org>.
3. Set `OWNER_ID` to your **numeric Telegram user ID**. This is the admin identity; the contact username is not used for authorization.
4. Set your watermark with `/setwm text` or **Settings**. The default watermark is empty so the bot won't silently stamp a placeholder.
5. Send a video or PDF. Use `/ddl URL` to process a direct video/PDF link.

## Telegram menu and group behavior

`/start` is intentionally short and rotates randomly through the five supplied quote cards, avoiding the same card twice in a row for each user. It sends the image, caption and inline keyboard as one photo message; other commands stay text-only to avoid clutter (except `/preview`, which returns the requested rendered preview). The old menu screenshot is a layout reference, not bot artwork. Buttons open Settings, Plans, My Plan, Usage, Help, Contact Admin and Channel; Admin Panel appears only for `OWNER_ID`. Bot-authored prose and menu captions use bold Unicode small-caps styling; labels and callback alerts use small-caps Unicode. Telegram does not support rich-text bold in inline button labels, but the button text remains small-caps and emoji-free. Set `START_BANNER_PATH` to pin `/start` to one image, or leave it blank to rotate through the five files under `assets/banners/`.

- Contact: [@ZCYT_2026](https://t.me/ZCYT_2026)
- Channel: [t.me/ZCYT_20266](https://t.me/ZCYT_20266)
- In a group, submissions from all members share the same sequential processing queue. The completed file is sent to the submitting user's private chat when possible. The user must open the bot with `/start` first; if private delivery is unavailable, the bot falls back to the group.
- For group file reception, disable group privacy for the bot via BotFather `/setprivacy` or make the bot an administrator. Keep `PUBLIC_MODE=true` to allow public use in private chats and groups; with it off, configure `AUTH_CHATS` or allowed users.

## Commands

| Command | Purpose |
|---|---|
| `/start` | Short menu |
| `/settings` | Text and output settings |
| `/setwm text` | Set watermark text |
| `/preview` | Preview the current design |
| `/plans` `/myplan` `/usage` | Plans, expiry and quota |
| `/ddl URL` | Process a direct video/PDF link; optional `URL \| new-name.mp4` |
| `/queue` `/cancel` | View or stop your jobs / cancel a prompt |
| `/status` | Server CPU, RAM, disk and queue status |
| `/reset` | Reset settings |
| `/help` `/id` | Help and numeric Telegram ID |

The old Quick Styles and `/style` command have been removed. Photo files are rejected; `/preview` may still use a Telegram thumbnail as a preview background.

### Owner-only administration

The owner can open **Admin Panel** from `/start` or use `/admin`. From the panel, they can review statistics/recent users, grant or renew plans, revoke to Free, and ban/unban users.

- `/grant USER_ID PLAN [DAYS]` — e.g. `/grant 123456789 plus 30`; paid plans default to 30 days. Use `permanent` for no expiry. Renewing the same active plan extends its expiry.
- `/revoke USER_ID`
- `/ban USER_ID` and `/unban USER_ID`

Plans: `free`, `plus`, `pro`, `infinity`. The numeric `OWNER_ID` is the sole admin authorization source.

## Install and deploy

### Docker

```bash
cp .env.example .env   # fill API_ID, API_HASH, BOT_TOKEN and OWNER_ID
docker compose up -d --build
docker compose logs -f
```

### Linux/macOS without Docker

Install Python 3.10+ and FFmpeg (for Debian/Ubuntu: `sudo apt install python3-venv ffmpeg fonts-noto-color-emoji`), then run `./run.sh` and fill in `.env` when prompted.

Railway uses the included root `Dockerfile`. Heroku uses `heroku.yml` and `Procfile`. Read [`docs/DEPLOY_RAILWAY_HEROKU.md`](docs/DEPLOY_RAILWAY_HEROKU.md) for environment variables, persistent-storage notes, and the warning not to run two polling workers with one token. Add a persistent volume for the SQLite database and Telegram session; PaaS container filesystems can be ephemeral.

## Resource and speed notes

The bot detects container CPU/RAM limits, defaults to one FFmpeg encoder and bounded FFmpeg threads, uses the `ultrafast` preset by default, and keeps the shared processing queue sequential. This is deliberately conservative for small Railway instances where the previous FFmpeg process was killed under memory pressure. Transfers and work files are bounded/cleaned within the bot's managed job directory.

Re-encoding is required to burn a watermark into video. Lower resolution or a faster preset reduces resource use; a lower CRF produces higher quality and larger output. If FFmpeg exits with code `-9`, check the platform memory limit and use a smaller resolution/preset.

## Limits and security

- Telegram bot uploads are capped at **2,000 MB**; Infinity is capped at the same amount in this build.
- PDF flattening renders pages as images, which increases size and removes selectable text.
- Direct-link downloads reject private/internal IP addresses and re-check redirects/DNS to reduce SSRF and rebinding risk.
- Never share `BOT_TOKEN`, API credentials or `.session` files. They are excluded from the distributable ZIP.

## Local engine and tests

```bash
python -m wmbot.cli movie.mp4 --text "@MyChannel" --set preset=ultrafast
python -m wmbot.cli book.pdf --text CONFIDENTIAL --set mode=tile
python -m wmbot.cli --list-settings

pip install -r requirements-dev.txt
pytest -q
```

The Telegram button styles require `hawkgram==1.0.9` (Pyrogram-compatible imports with Bot API button-style support). The suite includes real FFmpeg/PyMuPDF engine tests plus mocked Telegram/plan/queue tests.
