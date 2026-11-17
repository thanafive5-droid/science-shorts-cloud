FROM python:3.11-slim

# Install FFmpeg, fonts for burned-in Shorts captions, and ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-liberation \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY src/ ./src/
COPY scripts/ ./scripts/

# Persistent data directory (mount a cloud volume here for SQLite DB)
RUN mkdir -p /data /tmp/shorts_jobs

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data
ENV WORK_DIR=/tmp/shorts_jobs
ENV PORT=8080

EXPOSE 8080

CMD ["python", "-m", "src.main"]
