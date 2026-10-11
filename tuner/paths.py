"""Filesystem naming for an exFAT library.

exFAT rejects " * / : < > ? \\ | and control characters, and dislikes
trailing dots and spaces. Every path Tuner writes goes through here.

Task 5.1 adds channel_dir/final_paths/tmp_dir on top of sanitize().
"""

import os
import re

# Characters exFAT will not accept in a name, plus the control range.
FORBIDDEN_RE = re.compile(r'[\x00-\x1f\x7f"*/:<>?\\|]')
WHITESPACE_RE = re.compile(r"\s+")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# Keep the whole filename at or under this, extension included. Long
# YouTube titles routinely blow past filesystem limits otherwise.
MAX_FILENAME = 200
# Headroom for the longest sidecar suffix yt-dlp may produce, for example
# ".en-orig.srt", so the stem never has to shrink after the fact.
EXT_RESERVE = 16

TMP_DIRNAME = ".tuner-tmp"


class PathError(Exception):
    """A path could not be built safely."""


def sanitize(name, fallback="untitled"):
    """Make a single path component safe for exFAT.

    Not a security boundary on its own: separators are replaced here, and
    every delete additionally checks containment against the library root.
    """
    text = "" if name is None else str(name)
    text = FORBIDDEN_RE.sub("-", text)
    text = WHITESPACE_RE.sub(" ", text)
    # Leading dots would hide the folder; trailing dots and spaces are
    # silently dropped by some tools and confuse comparisons.
    text = text.strip().strip(".").strip()
    return text or fallback


class FilePlan(object):
    """Where one video and its sidecars belong."""

    def __init__(self, directory, stem):
        self.directory = directory
        self.stem = stem
        self.video = os.path.join(directory, stem + ".mp4")

    def sidecar(self, suffix):
        """A companion file sharing the stem, for example '.en.srt'."""
        if not suffix.startswith("."):
            suffix = "." + suffix
        return os.path.join(self.directory, self.stem + suffix)

    def __repr__(self):
        return "<FilePlan %s>" % self.video


def library_root(cfg):
    return os.path.abspath(str(cfg.get("library_root") or ""))


def channel_dir(cfg, channel_row, create=True):
    """The folder for one channel, created on demand."""
    folder = sanitize(channel_row["folder"] if channel_row is not None else None,
                      fallback="unknown-channel")
    path = os.path.join(library_root(cfg), folder)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _date_prefix(video_row):
    for key in ("published_at", "discovered_at"):
        try:
            value = video_row[key]
        except (KeyError, IndexError, TypeError):
            value = None
        if value:
            return str(value)[:10]
    return None


def build_stem(title, video_id, date=None, max_filename=MAX_FILENAME):
    """`<YYYY-MM-DD> - <title> [<id>]`, truncated to fit.

    The id token is never dropped: it is what matches a file back to a
    database row, and what Jellyfin's played list is matched on.
    """
    if not VIDEO_ID_RE.match(str(video_id or "")):
        raise PathError("unusable video id: %r" % (video_id,))

    token = "[%s]" % video_id
    prefix = "%s - " % date if date else ""
    room = max_filename - EXT_RESERVE - len(prefix) - len(token) - 1

    clean = sanitize(title)
    if room < 1:
        # Pathological, but never produce a nameless file.
        return (prefix + token).strip()
    if len(clean) > room:
        clean = clean[:room].rstrip(" .-")
        clean = clean or "untitled"

    return "%s%s %s" % (prefix, clean, token)


def final_paths(cfg, channel_row, video_row, create=True):
    """Where this video lands once the download finishes."""
    directory = channel_dir(cfg, channel_row, create=create)
    stem = build_stem(video_row["title"], video_row["video_id"],
                      date=_date_prefix(video_row))
    return FilePlan(directory, stem)


def tmp_dir(cfg, video_id, create=True):
    """Scratch space for one download.

    On the same filesystem as the library on purpose, so finishing a
    download is a rename rather than a copy across devices, and so nothing
    large is ever written to the 13 GB root.
    """
    if not VIDEO_ID_RE.match(str(video_id or "")):
        raise PathError("unusable video id: %r" % (video_id,))
    path = os.path.join(library_root(cfg), TMP_DIRNAME, str(video_id))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def tmp_root(cfg):
    return os.path.join(library_root(cfg), TMP_DIRNAME)
