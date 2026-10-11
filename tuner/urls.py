"""YouTube URL parsing.

Pure functions, no subprocess and no network. The add field accepts every
form a person might paste, and rejects anything that is not YouTube so the
resolver can never be pointed at another site.
"""

import re
try:                                    # pragma: no cover - stdlib shim
    from urllib.parse import parse_qs, urlparse
except ImportError:                     # pragma: no cover
    from urlparse import parse_qs, urlparse

ALLOWED_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")

# Channel tabs that may trail a channel URL and mean nothing to us.
TABS = ("videos", "streams", "shorts", "featured", "about", "playlists",
        "community", "podcasts", "releases", "store")

CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{8,}$")
BARE_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{1,60}$")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

CHANNEL_ID = "channel_id"
HANDLE = "handle"
LEGACY = "legacy"
VIDEO = "video"
UNKNOWN = "unknown"


def _result(kind, value, canonical_url):
    return {"kind": kind, "value": value, "canonical_url": canonical_url}


def _unknown(reason):
    return {"kind": UNKNOWN, "value": None, "canonical_url": None,
            "reason": reason}


def _strip_tabs(segments):
    while segments and segments[-1].lower() in TABS:
        segments = segments[:-1]
    return segments


def parse(raw):
    """Turn anything a person might paste into a resolvable reference."""
    if raw is None:
        return _unknown("empty input")

    text = " ".join(str(raw).split()).strip()
    if not text:
        return _unknown("empty input")

    # Bare forms, before any URL handling.
    if HANDLE_RE.match(text):
        return _result(HANDLE, text, "https://www.youtube.com/" + text)
    if BARE_CHANNEL_ID_RE.match(text):
        return _result(CHANNEL_ID, text,
                       "https://www.youtube.com/channel/" + text)

    candidate = text
    if "://" not in candidate:
        candidate = "https://" + candidate.lstrip("/")

    try:
        parts = urlparse(candidate)
    except ValueError:
        return _unknown("not a URL")

    host = (parts.netloc or "").lower().split("@")[-1].split(":")[0]
    if host not in ALLOWED_HOSTS:
        return _unknown("not a YouTube address: %s" % (host or text))

    query = parse_qs(parts.query or "")
    segments = [seg for seg in (parts.path or "").split("/") if seg]

    # youtu.be/<video id>
    if host == "youtu.be":
        if segments and VIDEO_ID_RE.match(segments[0]):
            return _result(VIDEO, segments[0], watch_url(segments[0]))
        return _unknown("no video id in the short link")

    # The RSS feed itself, so an existing feed URL can be pasted back in.
    if segments[:2] == ["feeds", "videos.xml"]:
        ids = query.get("channel_id") or []
        if ids and CHANNEL_ID_RE.match(ids[0]):
            return _result(CHANNEL_ID, ids[0],
                           "https://www.youtube.com/channel/" + ids[0])
        return _unknown("feed URL carries no channel_id")

    if not segments:
        return _unknown("no channel or video in the address")

    head = segments[0]

    # /watch?v=<id>
    if head == "watch":
        ids = query.get("v") or []
        if ids and VIDEO_ID_RE.match(ids[0]):
            return _result(VIDEO, ids[0], watch_url(ids[0]))
        return _unknown("no video id in the watch link")

    # /shorts/<id>, /live/<id>, /embed/<id> are videos, not channel tabs.
    if head in ("shorts", "live", "embed", "v"):
        if len(segments) > 1 and VIDEO_ID_RE.match(segments[1]):
            return _result(VIDEO, segments[1], watch_url(segments[1]))
        return _unknown("no video id in the %s link" % head)

    # /channel/<UC...>
    if head == "channel":
        if len(segments) > 1 and CHANNEL_ID_RE.match(segments[1]):
            return _result(CHANNEL_ID, segments[1],
                           "https://www.youtube.com/channel/" + segments[1])
        return _unknown("no channel id in the address")

    # /@handle
    if head.startswith("@"):
        if HANDLE_RE.match(head):
            return _result(HANDLE, head, "https://www.youtube.com/" + head)
        return _unknown("malformed handle")

    # /c/<name> and /user/<name>
    if head in ("c", "user"):
        rest = _strip_tabs(segments[1:])
        if rest:
            return _result(LEGACY, rest[0],
                           "https://www.youtube.com/%s/%s" % (head, rest[0]))
        return _unknown("no channel name in the address")

    return _unknown("unrecognized YouTube address")


def feed_url(channel_id):
    return "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id


def watch_url(video_id):
    return "https://www.youtube.com/watch?v=" + video_id
