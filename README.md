# ZC Watermark Bot — Render Background Worker

Telegram bot for text-watermarking videos and PDFs, with a per-user source-to-target channel automation workflow for the owner and Infinity-plan users. Automation watermarks only videos/PDFs and copies all other channel posts unchanged without a Telegram "Forwarded from" tag.

## Channel automation

Open `/automation` (or `/start` → **Channel Automation**; the owner also finds it in **Admin Panel**). It is included for the numeric `OWNER_ID` and for every user on the **Infinity** plan — each person configures their own channels, queue and watermark settings.

1. Set Source and Set Target by forwarding a post from each channel to the bot or by entering its `@username`/numeric channel ID.
2. Add the bot as an administrator in both channels. It needs to receive source posts and have permission to post in the target. Use **Check Access** before starting.
3. Set your own watermark settings. Automation uses your settings — videos use the video profile, PDFs use the independent PDF profile. Your plan's file-size limit applies.
4. For a first run with old posts, turn on **Backfill old posts**, then press **Start**. Forward old source posts into the bot's private chat; new posts are also queued while backfill is on, but processing waits. Press **Finish backfill** to release the queue oldest-source-message-ID first. The bot-token session does not enumerate the channel's old history.
5. If there are no old posts to add, press **Start** directly. **Pause** lets the current item finish and keeps queued items, but new channel posts are ignored while paused.
6. Use **Queue / status**, **Retry failed**, or **Skip failed** to manage the queue.

Only videos and PDFs are watermarked: videos by FFmpeg and PDFs by PyMuPDF. Every other source post—including plain text, photos, stickers, audio/voice notes, and non-PDF documents such as `.py` or APK files—is copied unchanged into the target without Telegram's "Forwarded from" tag. Watermarked videos/PDFs are uploaded as new posts; original captions and base filenames are kept where possible, but watermarking changes the media and may change the video container/metadata. Albums are handled as individual messages. Source-post edits are ignored. NSFW scanning is not included.

## Personal processing and settings

- Personal users can submit videos and PDFs. The bot does not offer photo/logo watermarking.
- Watermark processing uses one shared FFmpeg/PDF encode worker at a time; additional video/PDF tasks wait in the queue.
- `/start` first shows the supplied loading animation (the "Rose Three" loader, shipped as a generated GIF because Telegram cannot run HTML/JS), then sends the start photo with the greeting caption and menu buttons — always as fresh messages, never a reply/quote of the command. Tune or disable it with `START_LOADER_SECONDS`.
- Settings include a fully independent PDF watermark profile (own text, font, colours, opacity, outline, shadow, background box/border, pattern, position, rotation — nothing is inherited from the video profile after the one-time migration of old settings), explicit Background Box ON/OFF, font selection, layout and output controls. A PDF preview button renders the PDF profile on a sample page, and auto-contrast prevents light text from vanishing on white pages.
- The static video overlay aligns coordinates to even pixels to reduce chroma-alignment jitter. Choose **Static** to avoid intentional motion modes.

## Deploy on Render

1. Put `Dockerfile`, `requirements.txt`, `wmbot/`, and `assets/` at the repository root. Do not omit `assets/fonts/Poppins-Bold.ttf`; the Docker build checks for it.
2. Create or use a Render **Background Worker** with Docker runtime. No web port is served.
3. Connect the service to the GitHub repository/branch and enable Render's **Auto-Deploy on Commit** to avoid repeated manual ZIP uploads. Code changes will still trigger a build/deploy; environment/config changes may require a restart or deploy.
4. Set the required environment values from `.env.example` in Render: `API_ID`, `API_HASH`, `BOT_TOKEN`, and numeric `OWNER_ID`.
5. For persistent SQLite data and bot-token session storage, mount a persistent disk at `/data`. The container uses `/data` for app data and `/work` for temporary files.
6. Run only one worker using a given bot token. Never commit a real `.env`, token, API secret, database, or session file.

The ZIP contains the production source, runtime assets, tests, and development requirements. It does not contain credentials, session files, databases, or runtime media.
