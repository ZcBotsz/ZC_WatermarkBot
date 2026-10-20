#!/usr/bin/env bash
# One-command start without Docker (Linux/macOS). Needs Python 3.10+ and ffmpeg installed.
set -e
cd "$(dirname "$0")"
[ -f .env ] || { cp .env.example .env; echo "➡ Fill in .env first (nano .env), then run ./run.sh again"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
exec python -m wmbot
