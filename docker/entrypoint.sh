#!/usr/bin/env bash
# Container entrypoint: seed a config on first run, then hand over to Tuner.
set -euo pipefail

CONFIG="${TUNER_CONFIG:-/config/config.json}"
LIBRARY="${TUNER_LIBRARY:-/media/youtube}"
PORT="${TUNER_PORT:-8770}"
CONFIG_DIR="$(dirname "$CONFIG")"

mkdir -p "$CONFIG_DIR" "$LIBRARY"

# The database lives beside the config, on whatever the operator mapped to
# /config. It must never end up on the exFAT media drive: exFAT has no file
# locking and SQLite corrupts there. Refuse loudly rather than corrupt
# quietly if the two volumes were mapped to the same place.
case "$CONFIG_DIR/" in
  "$LIBRARY"/*)
    echo "!! $CONFIG_DIR is inside the library ($LIBRARY)." >&2
    echo "   Map /config to a normal Linux filesystem, not the media drive:" >&2
    echo "   SQLite has no file locking on exFAT and will corrupt." >&2
    exit 2
    ;;
esac

if [[ ! -f "$CONFIG" ]]; then
  echo "==> First run: writing $CONFIG"
  cat > "$CONFIG" <<EOF
{
  "title": "Tuner",
  "host": "0.0.0.0",
  "port": ${PORT},
  "library_root": "${LIBRARY}",
  "db_path": "${CONFIG_DIR}/tuner.db",
  "ytdlp_path": "yt-dlp",
  "ffmpeg_path": "ffmpeg",
  "poll_minutes": 30,
  "poll_jitter_minutes": 5,
  "feed_delay_seconds": 3,
  "discovery": "auto",
  "seed_n": 3,
  "keep_n": 5,
  "min_free_gb": 10,
  "shorts_max_seconds": 60,
  "skip_shorts": true,
  "skip_live": true,
  "subtitles": true,
  "prefer_h264": true,
  "thumbnails": true,
  "max_attempts": 3,
  "jellyfin_url": "",
  "jellyfin_api_key": "",
  "jellyfin_user_id": "",
  "watched_delete": false,
  "watched_grace_hours": 24,
  "jellyfin_poll_minutes": 15,
  "demo": false
}
EOF
  chmod 600 "$CONFIG"
fi

echo "==> yt-dlp $(yt-dlp --version 2>/dev/null || echo '(not found)')"
echo "==> library $LIBRARY"
echo "==> config  $CONFIG"

# "$@" lets `docker run ... --demo` or any other flag through.
exec python3 -m tuner --config "$CONFIG" "$@"
