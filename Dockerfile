FROM python:3.12-slim

# ffmpeg (x264/x265/aac), colour-emoji font, raqm (complex-script shaping), gcc (only needed if no prebuilt tgcrypto wheel)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-noto-color-emoji fontconfig libraqm0 gcc \
    && rm -rf /var/lib/apt/lists/* && fc-cache -f

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wmbot ./wmbot
COPY assets ./assets
RUN test -s /app/assets/fonts/Poppins-Bold.ttf \
    || (echo "Missing bundled fonts: make sure assets/fonts is committed to the repository." >&2; exit 1)

ENV PYTHONUNBUFFERED=1 DATA_DIR=/data WORK_DIR=/work
# Persistence is attached by the platform or docker-compose (Railway rejects VOLUME instructions).
CMD ["python", "-m", "wmbot"]
