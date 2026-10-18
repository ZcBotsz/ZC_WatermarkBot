# 🎬 Watermark Bot for Telegram

Send a **video, PDF or photo** → get it back with your watermark. Fast, high quality, and you design the watermark yourself from inside Telegram.

* **Videos** – text and/or logo, background box with opacity, border, outline, shadow, scrolling / bouncing / tiled modes, CRF quality, speed preset, resolution, H.264/H.265, audio copied untouched.
* **PDFs** – same designs; pick pages (`1-3,5`), put the mark over or under the content, optional *flatten* so it can't be removed.
* **Photos** – same designs.
* **Speed** – downloads, encoding and uploading happen in parallel (while video 1 uploads, video 2 is already encoding). The watermark is drawn once as a PNG, so it costs almost nothing per frame.

> Everything below was written for people who are **not** programmers. If you get stuck, send the error text to whoever set up your server.

---

## 1. What you need (5 minutes, once)

| What | Where to get it |
|---|---|
| `API_ID` and `API_HASH` | <https://my.telegram.org> → *API development tools* |
| `BOT_TOKEN` | Telegram → **@BotFather** → `/newbot` |
| `OWNER_ID` (your number) | Telegram → **@userinfobot** → it replies with your Id |
| A server | Any small Linux VPS. More CPU cores = faster videos. Put it in a region close to Telegram (e.g. Europe/Singapore) for the quickest transfers. |

⚠️ **Keep these secret.** Never put them in Google Drive, GitHub or a chat. If they ever leak: revoke the token in @BotFather (`/revoke`), regenerate the API hash at my.telegram.org, and delete any `.session` file.

## 2. Install – choose ONE way

### A) Docker (recommended)
```bash
# install docker once:  curl -fsSL https://get.docker.com | sh
cp .env.example .env        # then fill it in:  nano .env
docker compose up -d --build
docker compose logs -f      # you should see "online as @yourbot"
```
Update later: `git pull && docker compose up -d --build`. Your settings live in the `data/` folder – keep it.

### B) Without Docker (Linux / macOS)
Install Python 3.10+ and ffmpeg (`sudo apt install python3-venv ffmpeg fonts-noto-color-emoji`), then:
```bash
./run.sh        # first run creates .env → fill it in → run ./run.sh again
```
Keep it running in the background with `screen` or a systemd service.

## 3. Using the bot

1. Open your bot, send `/start`.
2. `/settings` → tap through the menus (text, colours, box, border, logo, position, video quality…). Tap **👁 Preview** any time.
3. Or tap `/style` for a one-tap look (Classic corner, Boxed label, Framed badge, Ghost centre, Diagonal tiles, News ticker, Bouncing logo).
4. **Send your files.** Send many at once – they queue, and you see a progress card with speed and ETA and a ✖ Cancel button.

| Command | What it does |
|---|---|
| `/settings` `/style` `/preview` | design & preview (reply to a video/photo to preview on it) |
| `/setwm text` | quick change of the watermark text |
| `/logo` | upload or remove your logo (PNG with transparency is best) |
| `/ddl URL` | watermark a file from a direct download link (`/ddl URL \| new name.mp4`) |
| `/queue` `/cancel` | see / stop your jobs (`/cancel` also aborts a pending text prompt) |
| `/status` | server CPU, RAM, disk, queue |
| `/reset` | back to default settings |
| `/id` | show your Telegram id |
| Admin: `/allow` `/disallow` `/ban` `/unban` `/authchat` `/unauthchat` `/stats` `/broadcast` | manage who may use the bot |

**Groups:** add the bot, then run `/authchat` in the group (admin only). Everyone there can use it; each person has their own settings and their own menus.

**Private by default.** Only you (OWNER_ID) can use it until you `/allow <id>` people or set `PUBLIC_MODE=true`.

### Every setting you can change
Text · font (9 + your own) · size · colour · opacity · UPPERCASE · outline · shadow · **background box** (colour, **opacity**, padding, rounded corners) · **border** (width, colour, opacity) · logo (size, opacity, placement) · mode (static / scrolling / bouncing / corner-hop / tiled) · 9 positions · margin · rotation · motion speed · "show every N s for M s" · video **CRF**, **preset**, codec, resolution, audio, container, GPU · send as video/document · PDF pages, over/under, flatten, strip metadata, auto-contrast · caption (keep/custom/none) · file name prefix/suffix · auto-start or confirm first.

Add fonts: drop `.ttf` / `.otf` files into `data/fonts/`, restart, they appear in the font list (★). Emoji and Indian-language text work (colour emoji need the Docker image or a system *Noto Color Emoji* font).

## 4. Speed – what to change if you want it even faster

Measured on a **2-core** test machine with a deliberately hard-to-compress 30 s Full-HD clip:

| Preset | Time | Size |
|---|---|---|
| `ultrafast` | 5 s (≈6× real-time) | 53 MB |
| `veryfast` *(default)* | 11 s | 17 MB |
| `medium` | 24 s | 20 MB |
| plain re-encode, **no** watermark, `veryfast` | 11.8 s | 17 MB |

So the watermark itself is free; the cost is x264. To go faster: lower preset (`superfast`/`ultrafast`) · resolution 720p · more CPU cores · an NVIDIA GPU (set *Hardware encoder = auto*, see the commented block in `docker-compose.yml`; falls back to CPU automatically if it fails). Real phone/camera videos usually encode faster than this test clip.

## 5. Limits you should know

* Telegram bots can send files up to **2 GB** (4 GB needs a Premium user-account, not supported).
* Re-encoding is needed to burn a watermark into video – there is no way to do it "without processing".
* MP4 output drops subtitle tracks (choose **MKV** in *Video output → Container* to keep them).
* *Flatten* PDFs is slower and makes bigger files (pages become pictures – the mark can't be removed, but text can't be selected).
* The bot does **not** download from "restricted/forwarding-disabled" channels. It processes files people send to it, or direct links you give it (private/internal addresses are blocked).

## 6. Try the engine without Telegram
```bash
python -m wmbot.cli movie.mp4 --text "@MyChannel" --style label --set preset=veryfast
python -m wmbot.cli book.pdf  --text CONFIDENTIAL --set mode=tile
python -m wmbot.cli --list-settings
```

## 7. For developers
`pip install -r requirements-dev.txt && pytest` (64 tests; uses real ffmpeg/PyMuPDF, mocks Telegram). Design notes: `docs/ARCHITECTURE.md`; what was learned from eight existing bots: `docs/RESEARCH.md`.

### Deploy to a cloud platform

Railway uses the included root `Dockerfile`. For Heroku, the project includes `heroku.yml` and a worker `Procfile`. Read [`docs/DEPLOY_RAILWAY_HEROKU.md`](docs/DEPLOY_RAILWAY_HEROKU.md) for deployment steps, required environment variables, persistent-storage notes, and the warning not to run two polling workers with the same bot token.
