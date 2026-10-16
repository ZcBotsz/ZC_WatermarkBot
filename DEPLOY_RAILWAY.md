# 🚂 Railway Deployment - Premium Watermark Bot

## Quick Deploy (1 Click)

### Step 1: Push to GitHub
```bash
git init
git add .
git commit -m "Ultra Watermark Bot Premium"
git branch -M main
git remote add origin https://github.com/yourname/ultra-watermark-bot.git
git push -u origin main
```

### Step 2: Railway
1. Go to https://railway.app → New Project → Deploy from GitHub
2. Select your repo `ultra-watermark-bot`
3. Railway auto detects `nixpacks.toml` with ffmpeg + python311
4. Add Environment Variables in Railway Dashboard → Variables:
```
API_ID=123456
API_HASH=your_hash
BOT_TOKEN=123456:ABC...
OWNER_ID=123456789
WORKER_COUNT=3
MAX_QUEUE=50
MAX_FILE_SIZE_MB=2000
DEFAULT_TEXT=@MyChannel • Join us
DEFAULT_MODE=bouncing
DEFAULT_POSITION=bottom_right
DEFAULT_PRESET=ultrafast
DEFAULT_CRF=23
PORT=8080  # Railway provides auto, but we handle health server
```
5. Deploy → Logs show `✅ Premium Bot Online!`

### Step 3: Health Check
Railway expects a web server on PORT. Our bot includes `health_server()` that binds to PORT env var:
- GET / → "🎬 Ultra Watermark Bot Premium is Running! ✅"
- GET /health → same

So Railway health check passes even though main work is Telegram polling.

### Step 4: Logs
Railway → Deployments → View Logs → should see:
```
[Engine] GPU: cpu
✅ Premium Config: Workers 3 | Queue 50 | Max 2000MB | Preset ultrafast | CRF 23
✅ Premium Bot Online! Send Video/PDF/Photo
🌐 Health server running on port 8080
```

## Heroku Deployment

1. Install Heroku CLI, login
2. 
```bash
heroku create ultra-watermark-bot
heroku buildpacks:add heroku/python
heroku buildpacks:add https://github.com/jonathanong/heroku-buildpack-ffmpeg-latest.git
git push heroku main
heroku ps:scale worker=1
heroku logs --tail
```
3. Add Config Vars in Heroku Dashboard → Settings → Config Vars: same as Railway env vars
4. Procfile already has `worker: python main.py`

## Render Deployment

1. Go to https://render.com → New → Worker
2. Connect GitHub repo
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python main.py`
5. Add Environment Variables same as Railway
6. Render uses `render.yaml` already included

## Premium Systematic Buttons - Not Messed Up

All buttons organized by category:

**Main Menu (2x2 grid):**
- Row1: Video, PDF (media types)
- Row2: Image, Set Logo (image)
- Row3: Settings, Config (system)
- Row4: Help, Speed Tips (info)

**Settings Menu (grouped):**
- Row1: Text, Mode (text)
- Row2: Position, Opacity (placement)
- Row3: Color, Font Size (visual)
- Row4: BG Box, Border (your request bg opacity + border)
- Row5: CRF Quality, Preset Speed (quality)
- Row6: Resolution, Codec (size)
- Row7: PDF Mode, Reset All (pdf + system)
- Row8: Back to Main

Each sub-menu has Back button, systematic, premium, not messed up.

## Testing After Deploy

1. In Telegram, send /start to your bot
2. Should see premium menu with 8 buttons systematic
3. Click Settings → should see 8 rows systematic
4. Send video → should watermark with bouncing mode default, bg black@0.5, border ON, ultrafast, CRF 23, bottom_right, white color, opacity 80%
5. Check logs in Railway for progress

## Troubleshooting Railway

- If build fails: Check nixpacks.toml has ffmpeg
- If bot not responding: Check BOT_TOKEN, API_ID, API_HASH correct
- If health check fails: PORT env var is set by Railway auto, our health_server binds to it
- If ffmpeg not found: nixpacks.toml includes ffmpeg in nixPkgs

## Cost

- Railway free tier: $5 credit, enough for small bot
- Heroku free tier removed, need Eco dyno $5/mo
- Render free worker sleeps after 15 min, need Starter $7/mo for always on

Recommended: Railway for now as you said, then Render for always on.

## Next Beautiful Step

After working on Railway, we make beautiful:
- Add web dashboard to change settings via browser (like CBML web/)
- Add checkered bg pattern option
- Add custom font upload
