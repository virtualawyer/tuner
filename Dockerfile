# Tuner -- YouTube subscription recorder for Jellyfin
#
# The app itself is Python standard library only. This image exists to
# supply the two external binaries it shells out to: yt-dlp and ffmpeg.

FROM python:3.12-slim-bookworm

# ffmpeg merges the separate 1080p video and audio streams YouTube serves,
# and embeds the thumbnail and metadata. It is not optional.
# ca-certificates and curl are needed to fetch yt-dlp below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# The standalone yt-dlp build, not the Debian package: the packaged one is
# usually months old and refuses to self-update, and YouTube breaks old
# versions regularly.
ARG YTDLP_VERSION=latest
RUN if [ "$YTDLP_VERSION" = "latest" ]; then \
        URL="https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"; \
    else \
        URL="https://github.com/yt-dlp/yt-dlp/releases/download/${YTDLP_VERSION}/yt-dlp"; \
    fi; \
    curl -fsSL "$URL" -o /usr/local/bin/yt-dlp \
    && chmod a+rx /usr/local/bin/yt-dlp \
    && /usr/local/bin/yt-dlp --version

WORKDIR /app
COPY tuner/ /app/tuner/
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# /config holds config.json and the SQLite database. It must be mapped to
# a real Linux filesystem, never to the exFAT media drive: exFAT has no
# file locking and SQLite corrupts on it.
# /media/youtube is the library.
VOLUME ["/config", "/media/youtube"]

ENV TUNER_CONFIG=/config/config.json \
    TUNER_LIBRARY=/media/youtube \
    TUNER_PORT=8770 \
    PYTHONUNBUFFERED=1

EXPOSE 8770

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import os,urllib.request; \
urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('TUNER_PORT','8770')+'/healthz', timeout=8)" \
    || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
