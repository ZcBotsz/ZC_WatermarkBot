FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg fonts-dejavu-core fonts-noto-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p temp/users temp/jobs downloads assets
CMD ["python", "main.py"]
