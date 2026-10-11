"""Configuration loading and validation.

Defaults live here. A config.json next to the app overrides them, and any
keys it carries that Tuner does not know about are preserved on save so a
hand-edited file never loses content.
"""

import json
import os

# Parent of the package directory: the repo root in development,
# /opt/tuner once installed.
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    "title": "Tuner",
    "host": "0.0.0.0",
    "port": 8770,
    "library_root": "/srv/media/youtube",
    "db_path": "/var/lib/tuner/tuner.db",
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
    "skip_shorts": True,
    "skip_live": True,
    "subtitles": True,
    "prefer_h264": True,
    "thumbnails": True,
    "max_attempts": 3,
    "jellyfin_url": "",
    "jellyfin_api_key": "",
    "jellyfin_user_id": "",
    "watched_delete": False,
    "watched_grace_hours": 24,
    "jellyfin_poll_minutes": 15,
    "demo": False,
}


class ConfigError(Exception):
    """Raised when the configuration cannot be used as given."""


def defaults():
    """A fresh copy of the shipped defaults."""
    return dict(DEFAULTS)


def load(path=None):
    """Return the defaults merged under the values in `path`.

    A missing file is not an error; Tuner runs on defaults. Passing None
    looks for config.json next to the app.
    """
    if path is None:
        path = DEFAULT_CONFIG_PATH

    cfg = defaults()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except ValueError as exc:
            raise ConfigError("%s is not valid JSON: %s" % (path, exc))
        except OSError as exc:
            raise ConfigError("cannot read %s: %s" % (path, exc))
        if not isinstance(data, dict):
            raise ConfigError("%s must contain a JSON object" % path)
        cfg.update(data)

    # Private (underscore) keys are runtime-only and stripped on save, so
    # remembering where this came from costs nothing.
    cfg["_config_path"] = path

    validate(cfg)
    return cfg


def validate(cfg):
    """Check the rules that make a config unusable rather than merely odd."""
    root = str(cfg.get("library_root") or "")
    db_path = str(cfg.get("db_path") or "")

    if not root:
        raise ConfigError("library_root must be set")
    if not db_path:
        raise ConfigError("db_path must be set")

    # The media drive is exFAT, which has no file locking. SQLite on it
    # corrupts. This is the one config mistake that loses data, so it is a
    # hard refusal rather than a warning.
    real_root = os.path.realpath(root)
    real_db = os.path.realpath(db_path)
    if real_db == real_root or real_db.startswith(real_root + os.sep):
        raise ConfigError(
            "db_path (%s) must not live under library_root (%s): the media "
            "drive is exFAT and has no file locking, which corrupts SQLite"
            % (db_path, root)
        )

    return cfg


def save(cfg, path=None):
    """Write the config atomically, dropping private keys."""
    if path is None:
        path = cfg.get("_config_path") or DEFAULT_CONFIG_PATH

    payload = dict((k, v) for k, v in cfg.items() if not k.startswith("_"))
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)

    # The file holds the Jellyfin API key, so it is created owner-only
    # rather than inheriting the umask, and the rename keeps it that way.
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(tmp, 0o600)          # a stale .tmp keeps its old mode otherwise
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)
    return path
