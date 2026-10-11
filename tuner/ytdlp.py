"""The yt-dlp adapter.

The only module that knows yt-dlp's command line or JSON shape. Every call
goes through _run, which always uses an argument list and never a shell
string.
"""

import collections
import json
import re
import subprocess
import time

from . import urls

PRINT_TEMPLATE = "%(channel_id)s|%(channel)s"

# A rate limit or bot check reads like an access failure but is temporary.
# Checked before the permanent patterns so a throttled probe is retried
# instead of skipping the video for good.
BOT_CHECK_RE = re.compile(
    r"confirm\s+you(?:'re| are)\s+not\s+a\s+bot|too many requests|http error 429",
    re.I)

# (pattern, reason) in priority order. A match means the video will never
# become downloadable, so it is skipped permanently rather than retried.
PERMANENT_PATTERNS = (
    # Not permanent in principle, but retrying now is pointless: it is a
    # premiere with a start time. Parking it as skipped costs no attempts
    # and leaves it one Download click away.
    (re.compile(r"live event will begin|premieres in|scheduled for", re.I),
     "upcoming"),
    (re.compile(r"private video", re.I), "private"),
    (re.compile(r"members[- ]only|join this channel", re.I), "members-only"),
    (re.compile(r"confirm your age|age[- ]restricted|inappropriate for some users",
                re.I), "age-restricted"),
    (re.compile(r"removed by the uploader|account (?:has been )?terminated",
                re.I), "removed"),
    (re.compile(r"copyright", re.I), "copyright"),
    (re.compile(r"unavailable", re.I), "unavailable"),
)

PROBE_FIELDS = ("id", "title", "duration", "live_status", "was_live", "width",
                "height", "upload_date", "timestamp", "channel_id", "channel",
                "availability")

# 1080p first as separate streams, then a pre-muxed 1080p-capped stream,
# then whatever exists. Everything ends up remuxed to mp4.
FORMAT_CHAIN = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"

# The same ceiling, but asking for H.264 before anything else. YouTube now
# serves AV1 as the best stream at 1080p: about 40% smaller, and undecodable
# by older clients. When a client cannot decode it the *server* transcodes,
# which on a repurposed Chromebook is the one thing this build exists to
# avoid. H.264 direct-plays on everything ever made.
FORMAT_CHAIN_H264 = (
    "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/"
    "bv*[height<=1080][vcodec^=avc1]+ba/"
    "b[height<=1080][vcodec^=avc1]/"
    "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b")


def format_chain(cfg):
    if cfg.get("prefer_h264", True):
        return FORMAT_CHAIN_H264
    return FORMAT_CHAIN

# [download]   4.2% of   12.34MiB at    1.23MiB/s ETA 00:09
PROGRESS_RE = re.compile(
    r"^\[download\]\s+(?P<percent>\d+(?:\.\d+)?)%\s+of\s+~?\s*"
    r"(?P<size>[\d.]+\s*[KMGTP]?i?B)"
    r"(?:\s+at\s+(?P<rate>[\d.]+\s*[KMGTP]?i?B/s|Unknown\s*B/s))?"
    r"(?:\s+(?:ETA|in)\s+(?P<eta>[\d:]+|Unknown))?")

# A line starting with one of these means the bytes are in and ffmpeg is
# working, so the UI should show a stage rather than a stalled percentage.
STAGE_PREFIXES = (
    ("[Merger]", "merging streams"),
    ("[VideoRemuxer]", "remuxing to mp4"),
    ("[Remux]", "remuxing to mp4"),
    ("[EmbedThumbnail]", "embedding thumbnail"),
    ("[Metadata]", "writing metadata"),
    ("[SubtitlesConvertor]", "converting subtitles"),
    ("[FixupM4a]", "fixing container"),
    ("[FixupM3u8]", "fixing container"),
    ("[ThumbnailsConvertor]", "converting thumbnail"),
)

TAIL_LINES = 40
PROGRESS_MIN_INTERVAL = 1.0


class YtdlpError(Exception):
    """Base for every failure this module reports."""


class ResolveError(YtdlpError):
    """A channel URL could not be turned into a channel id."""


class ProbeError(YtdlpError):
    """Base for probe failures."""

    def __init__(self, message, reason=None):
        YtdlpError.__init__(self, message)
        self.reason = reason


class PermanentProbeError(ProbeError):
    """The video will never be downloadable. Skip it for good."""


class TransientProbeError(ProbeError):
    """Something temporary. Try again later."""


def _run(cfg, args, timeout=60):
    """Invoke the binary. Returns (returncode, stdout, stderr).

    A missing binary or a timeout is reported through the return value
    rather than an exception, so callers classify failures in one place.
    """
    command = [cfg.get("ytdlp_path") or "yt-dlp"] + list(args)
    try:
        completed = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return (127, "", "yt-dlp not found at %s" % command[0])
    except subprocess.TimeoutExpired:
        return (124, "", "yt-dlp timed out after %ss" % timeout)
    except OSError as exc:
        return (126, "", "cannot run yt-dlp: %s" % exc)

    return (completed.returncode, completed.stdout or "", completed.stderr or "")


def version(cfg):
    """The binary's version string, or None when it does not resolve."""
    code, out, _ = _run(cfg, ["--version"], timeout=20)
    if code != 0:
        return None
    text = out.strip()
    return text or None


def available(cfg):
    return version(cfg) is not None


def update(cfg, timeout=180):
    """Run `yt-dlp -U`. Returns {ok, message, version}.

    YouTube breaks yt-dlp regularly, so this button matters more than it
    looks. A binary installed through apt refuses to self-update; that
    message is passed straight through rather than hidden, because the fix
    (install the standalone build) is the operator's to make.
    """
    before = version(cfg)
    if before is None:
        return {"ok": False, "version": None,
                "message": "yt-dlp is not installed, or not at %s"
                           % (cfg.get("ytdlp_path") or "yt-dlp")}

    code, out, err = _run(cfg, ["-U"], timeout=timeout)
    message = (out or "").strip() or (err or "").strip() or "no output"

    after = version(cfg)
    return {"ok": code == 0, "version": after, "message": message,
            "changed": bool(after and before and after != before)}


def _first_line(text):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _clean(value):
    """A usable string, or None. yt-dlp writes 'NA' for absent fields."""
    text = ("" if value is None else str(value)).strip()
    if not text or text.upper() == "NA":
        return None
    return text


def _channel_id_from(payload):
    """The UC id in a yt-dlp payload, wherever it happens to sit.

    `channel_id` is the usual home. On a channel tab the playlist `id` is
    the channel id itself. `uploader_id` is deliberately not consulted: for
    a handle URL it holds '@name', which is not an id.
    """
    for key in ("channel_id", "id"):
        candidate = _clean(payload.get(key))
        if candidate and urls.CHANNEL_ID_RE.match(candidate):
            return candidate
    return None


def _channel_title_from(payload):
    for key in ("channel", "uploader"):
        found = _clean(payload.get(key))
        if found:
            return found
    # Channel tabs are titled "Name - Videos"; the suffix is not the name.
    title = _clean(payload.get("title"))
    if title:
        for suffix in (" - Videos", " - Streams", " - Shorts", " - Home"):
            if title.endswith(suffix):
                return title[:-len(suffix)].strip() or None
    return title


def _resolve_json(cfg, args, timeout):
    """Run a -J command and return (payload, error_detail)."""
    code, out, err = _run(cfg, args, timeout=timeout)
    text = (out or "").strip()

    if code != 0 or not text:
        return (None, _first_line(err) or ("yt-dlp exited %d" % code))
    try:
        payload = json.loads(text)
    except ValueError:
        return (None, "yt-dlp sent something that is not JSON")
    if not isinstance(payload, dict):
        return (None, "unexpected reply from yt-dlp")
    return (payload, None)


def resolve_channel(cfg, parsed, timeout=90):
    """Turn a parsed reference into {channel_id, title, canonical_url}.

    A title of None means "not known yet, take it from the RSS feed". That
    is the normal outcome for a /channel/UC... URL, which needs no
    subprocess at all.

    Channel URLs are resolved from the **playlist level**, not from a video
    stub. An earlier version printed a per-entry template under
    --flat-playlist, and flat entries frequently carry no channel_id, which
    made perfectly ordinary handles fail to resolve.
    """
    if not parsed or parsed.get("kind") == urls.UNKNOWN:
        raise ResolveError(parsed.get("reason") if parsed
                           else "nothing to resolve")

    kind = parsed["kind"]
    if kind == urls.CHANNEL_ID:
        return {"channel_id": parsed["value"], "title": None,
                "canonical_url": parsed["canonical_url"]}

    url = parsed["canonical_url"]
    if kind == urls.VIDEO:
        attempts = [["-J", "--no-playlist", "--no-warnings", url]]
    else:
        # `--playlist-items 0` selects no entries, so this is one quick
        # request for the channel's own metadata. The second attempt pulls
        # a single entry, which populates more fields on the rare layout
        # where the first comes back bare.
        attempts = [
            ["-J", "--flat-playlist", "--playlist-items", "0",
             "--no-warnings", url],
            ["-J", "--flat-playlist", "--playlist-items", "1",
             "--no-warnings", url],
        ]

    last_error = None
    for args in attempts:
        payload, last_error = _resolve_json(cfg, args, timeout)
        if payload is None:
            continue

        channel_id = _channel_id_from(payload)
        if not channel_id:
            # A flat playlist may hide the id one level down, on an entry.
            for entry in (payload.get("entries") or []):
                if isinstance(entry, dict):
                    channel_id = _channel_id_from(entry)
                    if channel_id:
                        break

        if channel_id:
            return {
                "channel_id": channel_id,
                "title": _channel_title_from(payload),
                "canonical_url": "https://www.youtube.com/channel/" + channel_id,
            }
        last_error = None

    if last_error:
        raise ResolveError(last_error)
    raise ResolveError("yt-dlp returned no channel id for %s" % url)


BROWSE_MAX = 500


def list_recent(cfg, channel_id, limit=15, timeout=120):
    """The channel's newest videos, straight from yt-dlp.

    A stand-in for the RSS feed. YouTube fences the /feeds/ endpoint off
    from anything it takes for a bot, and once a host is flagged every
    channel returns 404 or 500 there while yt-dlp keeps working. Flat
    entries carry no publication date, so `published` comes back None and
    the probe fills it in later from upload_date.
    """
    url = (channel_id if "://" in str(channel_id)
           else "https://www.youtube.com/channel/" + channel_id)
    args = ["-J", "--flat-playlist", "--playlist-items",
            "1-%d" % max(1, int(limit)), "--no-warnings", url]

    payload, error = _resolve_json(cfg, args, timeout)
    if payload is None:
        raise ResolveError(error or "yt-dlp returned nothing")

    entries = []
    for entry in (payload.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        video_id = _clean(entry.get("id"))
        if not video_id or not urls.VIDEO_ID_RE.match(video_id):
            continue
        entries.append({
            "video_id": video_id,
            "title": _clean(entry.get("title")) or "(untitled)",
            "duration": _as_int(entry.get("duration")),
            "published": None,
            "thumbnail": None,
        })

    return {
        "channel_id": _channel_id_from(payload) or channel_id,
        "channel_title": _channel_title_from(payload),
        "entries": entries,
    }


def list_channel(cfg, url, limit=100, timeout=300):
    """A slice of a channel's whole lineup, newest first.

    Browsing is deliberately bounded. A channel with several thousand
    uploads would otherwise take minutes and return a payload nobody can
    read; the page asks for more when it wants more.
    """
    limit = max(1, min(int(limit or 100), BROWSE_MAX))
    return list_recent(cfg, url, limit=limit, timeout=timeout)


def classify_failure(stderr, code=None):
    """Decide whether a failure is worth retrying.

    Returns (permanent, reason). The bot check is deliberately transient:
    treating a rate limit as permanent would skip real videos for good.
    """
    text = stderr or ""
    if BOT_CHECK_RE.search(text):
        return (False, "rate-limited")
    for pattern, reason in PERMANENT_PATTERNS:
        if pattern.search(text):
            return (True, reason)
    if code == 124:
        return (False, "timeout")
    return (False, "error")


def _as_int(value):
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize(payload):
    """Keep only the fields the build reads, in the shapes it expects."""
    meta = dict((field, payload.get(field)) for field in PROBE_FIELDS)
    meta["duration"] = _as_int(meta.get("duration"))
    meta["width"] = _as_int(meta.get("width"))
    meta["height"] = _as_int(meta.get("height"))
    meta["timestamp"] = _as_int(meta.get("timestamp"))
    meta["was_live"] = bool(meta.get("was_live"))
    meta["live_status"] = meta.get("live_status") or "not_live"
    meta["title"] = meta.get("title") or "(untitled)"
    return meta


def probe(cfg, video_id, timeout=90):
    """Metadata for one video, normalized. Raises on failure."""
    args = ["-J", "--no-playlist", "--no-warnings", urls.watch_url(video_id)]
    code, out, err = _run(cfg, args, timeout=timeout)

    if code != 0:
        permanent, reason = classify_failure(err, code)
        detail = _first_line(err) or ("yt-dlp exited %d" % code)
        if permanent:
            raise PermanentProbeError(detail, reason=reason)
        raise TransientProbeError(detail, reason=reason)

    text = (out or "").strip()
    if not text:
        raise TransientProbeError("yt-dlp returned no metadata", reason="empty")

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise TransientProbeError("unreadable metadata: %s" % exc,
                                  reason="malformed")

    if not isinstance(payload, dict):
        raise TransientProbeError("unexpected metadata shape",
                                  reason="malformed")

    return normalize(payload)


def download_args(cfg, video_id, tmp):
    """The exact argument list, built as a list and never a shell string."""
    args = [
        "--no-playlist", "--no-warnings", "--newline", "--no-colors",
        "--ignore-config",
        # A subtitle fetch getting 429'd used to fail the whole video.
        "--retries", "5", "--fragment-retries", "10", "--sleep-requests", "1",
        "--windows-filenames", "--trim-filenames", "200",
        "--format", format_chain(cfg),
        "--merge-output-format", "mp4", "--remux-video", "mp4",
    ]

    if cfg.get("subtitles", True):
        args.extend(["--write-subs", "--write-auto-subs",
                     "--sub-langs", "en.*,-live_chat",
                     "--convert-subs", "srt"])

    if cfg.get("thumbnails", True):
        # Written as a sidecar as well as embedded: Jellyfin reads a
        # matching .jpg beside the video, and does not reliably pick up
        # cover art from inside an mp4. Converted because YouTube serves
        # webp, which Jellyfin handles poorly as a poster.
        args.extend(["--write-thumbnail", "--convert-thumbnails", "jpg"])

    args.extend([
        "--embed-thumbnail", "--embed-metadata",
        "--paths", "home:" + tmp,
        "--paths", "temp:" + tmp,
        "--output", "%(id)s.%(ext)s",
        urls.watch_url(video_id),
    ])
    return args


def parse_progress(line):
    """Turn one yt-dlp output line into a progress update, or None."""
    text = (line or "").rstrip()

    for prefix, stage in STAGE_PREFIXES:
        if text.startswith(prefix):
            return {"stage": stage}

    match = PROGRESS_RE.match(text)
    if not match:
        return None

    found = match.groupdict()
    rate = (found.get("rate") or "").strip() or None
    eta = (found.get("eta") or "").strip() or None
    return {
        "percent": float(found["percent"]),
        "size": found["size"].strip(),
        "rate": None if rate in (None, "Unknown B/s") else rate,
        "eta": None if eta in (None, "Unknown") else eta,
        "stage": "downloading",
    }


def download(cfg, video_id, tmp, on_progress=None, on_start=None, timeout=None):
    """Run the download. Returns (returncode, tail).

    `tail` is the last few lines of combined output, which is what goes in
    the row's error field when something fails.
    """
    command = [cfg.get("ytdlp_path") or "yt-dlp"] + download_args(cfg, video_id, tmp)

    try:
        process = subprocess.Popen(
            command,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        return (127, "yt-dlp not found at %s" % command[0])
    except OSError as exc:
        return (126, "cannot run yt-dlp: %s" % exc)

    if on_start is not None:
        on_start(process)

    tail = collections.deque(maxlen=TAIL_LINES)
    last_emit = 0.0
    last_stage = None

    try:
        for line in process.stdout:
            tail.append(line.rstrip())
            if on_progress is None:
                continue

            update = parse_progress(line)
            if not update:
                continue

            # Throttle to about one update a second, but never swallow a
            # stage change or the final 100%.
            now = time.time()
            interesting = (update.get("stage") != last_stage
                           or update.get("percent") == 100.0)
            if interesting or (now - last_emit) >= PROGRESS_MIN_INTERVAL:
                last_emit = now
                last_stage = update.get("stage")
                try:
                    on_progress(update)
                except Exception:   # a UI callback must not kill a download
                    pass
    finally:
        try:
            process.stdout.close()
        except (IOError, OSError):
            pass
        code = process.wait(timeout=timeout) if timeout else process.wait()

    return (code, "\n".join(tail))
