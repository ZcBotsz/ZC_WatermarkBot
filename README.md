# ZC Watermark Bot — Render Background Worker

Telegram bot for text-watermarking videos and PDFs, with an owner-controlled source-to-target channel automation workflow. Photos are not watermarked; channel photos are forwarded unchanged when automation is enabled.

## Channel automation

Open `/start` → **Admin Panel** → **Channel Automation**. Only the numeric `OWNER_ID` can configure it.

1. Set Source and Set Target by forwarding a post from each channel to the bot or by entering its `@username`/numeric channel ID.
2. Add the bot as an administrator in both channels. It needs to receive source posts and have permission to post in the target. Use **Check Access** before starting.
3. Set the owner's normal watermark settings. Automation uses these settings, including the separate PDF watermark-text override.
4. For a first run with old posts, turn on **Backfill old posts**, then press **Start**. Forward old source posts into the bot's private chat; new posts are also queued while backfill is on, but processing waits. Press **Finish backfill** to release the queue oldest-source-message-ID first. The bot-token session does not enumerate the channel's old history.
5. If there are no old posts to add, press **Start** directly. **Pause** lets the current item finish and keeps queued items, but new channel posts are ignored while paused.
6. Use **Queue / status**, **Retry failed**, or **Skip failed** to manage the queue.

Photos are forwarded unchanged and may show Telegram's source attribution. Videos are downloaded, watermarked by FFmpeg, and uploaded as new posts; PDFs are watermarked by PyMuPDF and uploaded as new posts. Original captions and base filenames are kept where possible, but watermarking changes the media and may change video container/metadata. Albums are handled as individual messages. Source-post edits are ignored. NSFW scanning is not included.

## Personal processing and settings

- Personal users can submit videos and PDFs. The bot does not offer photo/logo watermarking.
- Watermark processing uses one shared FFmpeg/PDF encode worker at a time; additional video/PDF tasks wait in the queue.
- `/start` sends a plain text menu as a new message, not as a reply and not as a photo.
- Settings include a PDF-specific watermark-text override, explicit Background Box ON/OFF, font selection, layout and output controls.
- The static video overlay aligns coordinates to even pixels to reduce chroma-alignment jitter. Choose **Static** to avoid intentional motion modes.

## Deploy on Render

1. Put `Dockerfile`, `requirements.txt`, `wmbot/`, and `assets/` at the repository root. Do not omit `assets/fonts/Poppins-Bold.ttf`; the Docker build checks for it.
2. Create or use a Render **Background Worker** with Docker runtime. No web port is served.
3. Connect the service to the GitHub repository/branch and enable Render's **Auto-Deploy on Commit** to avoid repeated manual ZIP uploads. Code changes will still trigger a build/deploy; environment/config changes may require a restart or deploy.
4. Set the required environment values from `.env.example` in Render: `API_ID`, `API_HASH`, `BOT_TOKEN`, and numeric `OWNER_ID`.
5. For persistent SQLite data and bot-token session storage, mount a persistent disk at `/data`. The container uses `/data` for app data and `/work` for temporary files.
6. Run only one worker using a given bot token. Never commit a real `.env`, token, API secret, database, or session file.

The ZIP contains the production source, runtime assets, tests, and development requirements. It does not contain credentials, session files, databases, or runtime media.
