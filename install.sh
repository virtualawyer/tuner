#!/usr/bin/env bash
# Install Tuner as a systemd service on Debian.
# Run from inside the tuner folder:  sudo bash install.sh
#
# Use `bash install.sh`, not `./install.sh`: the exec bit is lost when the
# folder is copied through the exFAT media drive.
set -euo pipefail

DEST=/opt/tuner
STATE=/var/lib/tuner
SRC="$(cd "$(dirname "$0")" && pwd)"

fail() { echo "!!  $1" >&2; [ $# -gt 1 ] && echo "    fix: $2" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
  fail "Please run with sudo:  sudo bash install.sh"
fi

# --- preflight -------------------------------------------------------------
# Everything is checked before a single file is copied, so a failed install
# leaves nothing half-written.

echo "==> Preflight"

command -v python3 >/dev/null 2>&1 \
  || fail "python3 not found" "apt install python3"

python3 - <<'PY' || fail "python3 is older than 3.9" "apt install python3"
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
echo "    python3 $(python3 -c 'import platform;print(platform.python_version())')"

# Read the config that will be installed, falling back to the shipped one.
CFG="$DEST/config.json"
[[ -f "$CFG" ]] || CFG="$SRC/config.json"
read_cfg() { python3 -c "import json;print(json.load(open('$CFG')).get('$1',''))" 2>/dev/null || echo ""; }

PORT="$(read_cfg port)";            PORT="${PORT:-8770}"
LIBRARY="$(read_cfg library_root)"; LIBRARY="${LIBRARY:-/srv/media/youtube}"
DBPATH="$(read_cfg db_path)";       DBPATH="${DBPATH:-$STATE/tuner.db}"
YTDLP="$(read_cfg ytdlp_path)";     YTDLP="${YTDLP:-yt-dlp}"
FFMPEG="$(read_cfg ffmpeg_path)";   FFMPEG="${FFMPEG:-ffmpeg}"
MINFREE="$(read_cfg min_free_gb)";  MINFREE="${MINFREE:-10}"

command -v "$YTDLP" >/dev/null 2>&1 || [[ -x "$YTDLP" ]] \
  || fail "yt-dlp not found ($YTDLP)" \
"curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp && chmod a+rx /usr/local/bin/yt-dlp
         (the apt package is usually too old; YouTube breaks it regularly)"
echo "    yt-dlp $("$YTDLP" --version 2>/dev/null || echo '(version unknown)')"

command -v "$FFMPEG" >/dev/null 2>&1 || [[ -x "$FFMPEG" ]] \
  || fail "ffmpeg not found ($FFMPEG)" \
          "apt install ffmpeg   (needed to merge 1080p streams and embed metadata)"
echo "    ffmpeg present"

# The database must not sit on exFAT, which has no file locking.
case "$DBPATH" in
  "$LIBRARY"|"$LIBRARY"/*)
    fail "db_path ($DBPATH) is inside library_root ($LIBRARY)" \
         "the media drive is exFAT and has no file locking, which corrupts SQLite; use $STATE/tuner.db"
    ;;
esac

MOUNT_PARENT="$(dirname "$LIBRARY")"
if ! mountpoint -q "$MOUNT_PARENT" 2>/dev/null && [[ ! -d "$MOUNT_PARENT" ]]; then
  fail "the library parent ($MOUNT_PARENT) does not exist" \
       "mount the media drive first, then re-run"
fi

FREE_GB=$(df -BG --output=avail "$MOUNT_PARENT" 2>/dev/null | tail -1 | tr -dc '0-9' || echo 0)
if [[ -n "$FREE_GB" && "$FREE_GB" -lt "$MINFREE" ]]; then
  fail "only ${FREE_GB}G free on $MOUNT_PARENT, below min_free_gb (${MINFREE}G)" \
       "free some space, or lower min_free_gb in config.json"
fi
echo "    ${FREE_GB}G free on $MOUNT_PARENT"

# --- install ---------------------------------------------------------------

echo "==> Installing to $DEST"
mkdir -p "$DEST" "$STATE" "$LIBRARY"
rm -rf "${DEST:?}/tuner"
cp -r "$SRC/tuner" "$DEST/"
cp "$SRC/tuner.service" "$DEST/"
# Never clobber an edited config on reinstall.
if [[ ! -f "$DEST/config.json" ]]; then
  cp "$SRC/config.json" "$DEST/"
fi
chmod 600 "$DEST/config.json"
find "$DEST/tuner" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> Installing systemd unit"
cp "$SRC/tuner.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tuner.service

sleep 1
if ! systemctl is-active --quiet tuner.service; then
  echo "!!  The service did not stay up. Recent log:" >&2
  journalctl -u tuner -n 20 --no-pager >&2
  exit 1
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')

echo
echo "==> Done. Tuner is running."
echo "    URL:     http://${IP:-<server-ip>}:${PORT}"
echo "    Library: $LIBRARY"
echo "    Logs:    journalctl -u tuner -f"
echo
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "^Status: active"; then
  if ! ufw status 2>/dev/null | grep -q "${PORT}/tcp"; then
    echo "!!  The firewall is active and port ${PORT} is not open:"
    echo "    sudo ufw allow ${PORT}/tcp"
    echo
  fi
fi
echo "    Next: add the library to Jellyfin as $LIBRARY with metadata"
echo "    downloaders switched OFF, then add your first channel in the UI."
