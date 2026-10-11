"""What gets downloaded and what gets skipped.

Two rules ship: Shorts and livestreams. The engine is an ordered list of
small rule functions so a third rule is one line, but only these two are
enabled because only these two were asked for.
"""

ACCEPT = "accept"
SKIP = "skip"

# Channel columns that fall back to a global config key when NULL.
INHERITED = (
    ("skip_shorts", "skip_shorts", bool),
    ("skip_live", "skip_live", bool),
    ("keep_n", "keep_n", int),
    ("seed_n", "seed_n", int),
)

# Reasons, as stored in videos.skip_reason and shown in the UI.
SHORT = "short"
LIVE = "live"
UPCOMING = "upcoming"
WAS_LIVE = "was_live"

LIVE_NOW = ("is_live", "post_live")


def _rule_live(meta, settings):
    """Live, upcoming, and finished-stream archives.

    Checked before the Shorts rule so a stream is never reported as a
    Short, and so an in-progress broadcast never stalls the queue.
    """
    if not settings.get("skip_live"):
        return None

    status = meta.get("live_status") or "not_live"
    if status == "is_upcoming":
        return UPCOMING
    if status in LIVE_NOW:
        return LIVE
    if status == "was_live" or meta.get("was_live"):
        return WAS_LIVE
    return None


def _rule_short(meta, settings):
    """Vertical clips under the threshold.

    Duration alone decides when the dimensions are unknown, which is the
    common case for a probe that returned no format information.
    """
    if not settings.get("skip_shorts"):
        return None

    duration = meta.get("duration")
    if duration is None:
        return None

    limit = settings.get("shorts_max_seconds", 60)
    if duration > limit:
        return None

    width, height = meta.get("width"), meta.get("height")
    if width and height and height < width:
        return None      # short but landscape: an ordinary clip, not a Short

    return SHORT


RULES = (_rule_live, _rule_short)


def decide(meta, settings):
    """Return (action, reason). Reason is None when accepted."""
    meta = meta or {}
    settings = settings or {}
    for rule in RULES:
        reason = rule(meta, settings)
        if reason:
            return (SKIP, reason)
    return (ACCEPT, None)


def _column(row, name):
    """Read a column that may be absent from this row object."""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return None


def settings_for(cfg, channel_row=None):
    """Resolve the effective settings for one channel.

    A NULL column inherits the global config value; 0 or 1 overrides it.
    Nothing else in the codebase may read those columns directly, so
    inherit-versus-override lives in exactly one place.
    """
    cfg = cfg or {}
    resolved = {"shorts_max_seconds": int(cfg.get("shorts_max_seconds", 60) or 60)}

    for key, config_key, kind in INHERITED:
        value = _column(channel_row, key) if channel_row is not None else None
        if value is None:
            value = cfg.get(config_key)
        if kind is bool:
            resolved[key] = bool(value)
        else:
            try:
                resolved[key] = int(value or 0)
            except (TypeError, ValueError):
                resolved[key] = 0

    return resolved
