# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# ffmpeg is needed at runtime for voice playback (music.py transcodes the
# yt-dlp audio stream into PCM for Discord). git is needed by entrypoint.sh,
# which runs `git pull` on every start - see the README's "Updates" section
# and bot/modules/updater.py. Installed before the pip layer since it
# changes far less often, and before USER botuser since apt-get needs root.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

# Install deps first so this layer is cached unless requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The whole repo is baked in (not just bot/) - entrypoint.sh needs a real
# git checkout with an `origin` remote to pull from at startup. .dockerignore
# excludes .env and other local/secret files but deliberately keeps .git.
COPY . .

# SQLite file + anything else persistent lives here - mount this to a
# TrueNAS dataset in docker-compose.yml so it survives container recreation
VOLUME ["/app/data"]

# Runs as a non-root user with a fixed UID/GID so you can chown the host
# dataset to match (see README's TrueNAS deployment section)
RUN groupadd --gid 1000 botuser \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/false botuser \
    && chown -R botuser:botuser /app \
    && chmod +x entrypoint.sh
USER botuser

ENTRYPOINT ["./entrypoint.sh"]
