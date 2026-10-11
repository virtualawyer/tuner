"""YouTube RSS: fetching and parsing.

The only module that makes an HTTP request to YouTube. Conditional GET is
not optional politeness here -- every enabled channel is polled on a cycle,
and a 304 costs YouTube almost nothing.
"""

import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree

from . import USER_AGENT, urls

TIMEOUT = 20

ATOM = "http://www.w3.org/2005/Atom"
YT = "http://www.youtube.com/xml/schemas/2015"
MEDIA = "http://search.yahoo.com/mrss/"
NS = {"atom": ATOM, "yt": YT, "media": MEDIA}

# Tests replace this with a stub. Nothing else may open a connection.
OPENER = None


class FeedError(Exception):
    """A feed could not be fetched or read."""

    def __init__(self, message, status=None):
        Exception.__init__(self, message)
        self.status = status


def _open(request, timeout):
    if OPENER is not None:
        return OPENER(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def fetch(channel_id, etag=None, last_modified=None, timeout=TIMEOUT):
    """Fetch a channel feed.

    Returns (status, body, etag, last_modified). A 304 returns the stored
    validators unchanged and no body, which is the common case on a quiet
    channel.
    """
    request = urllib.request.Request(urls.feed_url(channel_id))
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/atom+xml, application/xml")
    if etag:
        request.add_header("If-None-Match", etag)
    if last_modified:
        request.add_header("If-Modified-Since", last_modified)

    try:
        response = _open(request, timeout)
    except urllib.error.HTTPError as exc:
        # 304 arrives here: it is not a 2xx, so urllib calls it an error.
        if exc.code == 304:
            return (304, None, etag, last_modified)
        raise FeedError("feed returned HTTP %d" % exc.code, status=exc.code)
    except urllib.error.URLError as exc:
        raise FeedError("cannot reach YouTube: %s" % exc.reason)
    except OSError as exc:
        raise FeedError("cannot reach YouTube: %s" % exc)

    try:
        status = getattr(response, "status", None) or response.getcode()
        if status == 304:
            return (304, None, etag, last_modified)

        raw = response.read()
        headers = getattr(response, "headers", {}) or {}
        get = headers.get if hasattr(headers, "get") else (lambda k, d=None: d)
        new_etag = get("ETag", None)
        new_modified = get("Last-Modified", None)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    if status != 200:
        raise FeedError("feed returned HTTP %s" % status, status=status)

    if isinstance(raw, bytes):
        body = raw.decode("utf-8", "replace")
    else:
        body = raw

    return (200, body, new_etag or etag, new_modified or last_modified)


def _text(node, path):
    found = node.find(path, NS)
    if found is None:
        return None
    return (found.text or "").strip() or None


def parse(body):
    """Read an Atom feed into {channel_id, channel_title, entries}.

    Entries come back newest first. Malformed XML raises FeedError rather
    than leaking a ParseError from the stdlib.
    """
    if not body:
        raise FeedError("empty feed body")

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise FeedError("feed is not valid XML: %s" % exc)

    channel_id = _text(root, "yt:channelId")
    channel_title = _text(root, "atom:title") or _text(root, "atom:author/atom:name")

    entries = []
    for node in root.findall("atom:entry", NS):
        video_id = _text(node, "yt:videoId")
        if not video_id:
            continue
        title = (_text(node, "atom:title")
                 or _text(node, "media:group/media:title")
                 or "(untitled)")
        thumbnail = None
        thumb_node = node.find("media:group/media:thumbnail", NS)
        if thumb_node is not None:
            thumbnail = thumb_node.get("url")

        entries.append({
            "video_id": video_id,
            "title": title,
            "published": _text(node, "atom:published"),
            "updated": _text(node, "atom:updated"),
            "thumbnail": thumbnail,
        })

    entries.sort(key=lambda item: item["published"] or "", reverse=True)

    return {"channel_id": channel_id, "channel_title": channel_title,
            "entries": entries}
