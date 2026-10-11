"""Discovery: turning a channel's feed into video rows.

Nothing here downloads anything. Discovery only records that a video
exists; the probe-and-filter pipeline decides what happens to it.
"""

import random
import time

from . import db, feed, filters, log, paths, urls, ytdlp


def pause(seconds, stop_event=None):
    """Wait between requests to YouTube. Interruptible.

    Tests replace this rather than sleeping for real.
    """
    if seconds <= 0:
        return False
    if stop_event is not None:
        return bool(stop_event.wait(seconds))
    time.sleep(seconds)
    return False


def feed_delay(cfg):
    """Seconds to leave between one channel's feed request and the next.

    Thirty channels polled back to back is thirty requests in two seconds,
    which YouTube answers with a mix of 404s and 500s however valid the
    channel ids are. Spacing them out is the whole fix; the poll interval
    was never the problem.
    """
    try:
        return max(0.0, float(cfg.get("feed_delay_seconds", 3)))
    except (TypeError, ValueError):
        return 3.0


def ingest(conn, cfg, channel_row):
    """Fetch a channel's feed and record any videos not seen before.

    Returns the number of new rows. Known video ids are left completely
    alone, including their status, so a video deleted on purpose never
    comes back just because it is still in the feed.
    """
    row_id = channel_row["id"]
    channel_id = channel_row["channel_id"]

    mode = str(cfg.get("discovery", "auto")).lower()
    parsed, trouble = None, None

    if mode in ("auto", "rss"):
        try:
            status, body, etag, modified = feed.fetch(
                channel_id, channel_row["etag"], channel_row["last_modified"])
            if status == 304:
                db.update_channel(conn, row_id, last_checked_at=log.now_iso(),
                                  last_error=None)
                return 0
            parsed = feed.parse(body)
        except feed.FeedError as exc:
            trouble = str(exc)

    # YouTube fences the /feeds/ endpoint off from anything it decides is a
    # bot, and once a host is flagged every channel 404s there. yt-dlp still
    # works, so it stands in rather than the channel simply going stale.
    if parsed is None and mode in ("auto", "ytdlp"):
        try:
            parsed = ytdlp.list_recent(cfg, channel_id)
            etag, modified = None, None
            if trouble:
                log.info(conn, "%s: feed unavailable (%s), listed with yt-dlp"
                         % (channel_row["title"], trouble),
                         channel_row_id=row_id)
            trouble = None
        except ytdlp.YtdlpError as exc:
            trouble = "%s; yt-dlp also failed: %s" % (trouble or "no feed", exc)

    if parsed is None:
        db.update_channel(conn, row_id, last_checked_at=log.now_iso(),
                          last_error=trouble or "could not list the channel")
        log.warn(conn, "could not list %s: %s" % (channel_row["title"], trouble),
                 channel_row_id=row_id)
        return 0

    added = 0
    for entry in parsed["entries"]:
        if db.get_video_by_ytid(conn, entry["video_id"]) is not None:
            continue
        inserted = db.add_video(conn, entry["video_id"], row_id, entry["title"],
                                published_at=entry["published"], status=db.NEW)
        if inserted is not None:
            added += 1

    updates = {
        "etag": etag,
        "last_modified": modified,
        "last_checked_at": log.now_iso(),
        "last_error": None,
    }
    # The feed is the authority on the channel's display name. The folder is
    # deliberately not renamed with it, so existing files stay where Jellyfin
    # already indexed them.
    feed_title = parsed.get("channel_title")
    if feed_title and feed_title != channel_row["title"]:
        updates["title"] = feed_title

    db.update_channel(conn, row_id, **updates)

    if added:
        log.info(conn, "%d new video%s from %s"
                 % (added, "" if added == 1 else "s",
                    feed_title or channel_row["title"]),
                 channel_row_id=row_id)
    return added


PRE_EXISTING = "pre-existing"


def seed(conn, cfg, channel_row_id, seed_n=None):
    """Keep only the newest N discovered videos eligible.

    This is what makes "grab N newest, then track forward" true without
    crawling the whole channel: the rest of the feed is marked skipped with
    reason `pre-existing`, so it is visible in the UI but never downloaded.
    """
    if seed_n is None:
        channel_row = db.get_channel(conn, channel_row_id)
        seed_n = filters.settings_for(cfg, channel_row).get("seed_n", 3)
    seed_n = max(0, int(seed_n))

    rows = db.list_videos(conn, status=db.NEW, channel_row_id=channel_row_id)
    skipped = 0
    for row in rows[seed_n:]:
        db.set_video_status(conn, row["id"], db.SKIPPED,
                            skip_reason=PRE_EXISTING)
        skipped += 1
    return {"kept": min(len(rows), seed_n), "skipped": skipped}


def backfill(conn, cfg, channel_row, count=None):
    """Make the newest `count` videos of a channel eligible again.

    Raising "download the last N" on a channel that has already been seeded
    only means anything if the videos it skipped can come back. Only rows
    skipped as `pre-existing` are revived: a Short or a livestream stays
    skipped, because a filter said so rather than the seed cutoff.
    """
    if count is None:
        count = filters.settings_for(cfg, channel_row).get("seed_n", 0)
    count = max(0, int(count or 0))
    if not count:
        return 0

    rows = db.list_videos(conn, channel_row_id=channel_row["id"], limit=count,
                          order="published_at DESC, id DESC")
    revived = 0
    for row in rows:
        if row["status"] != db.SKIPPED or row["skip_reason"] != PRE_EXISTING:
            continue
        db.set_video_status(conn, row["id"], db.NEW, skip_reason=None,
                            error=None)
        revived += 1

    if revived:
        log.info(conn, "%s: pulling back %d older video%s"
                 % (channel_row["title"], revived, "" if revived == 1 else "s"),
                 channel_row_id=channel_row["id"])
    return revived


def browse(conn, cfg, raw_url, limit=100):
    """List a channel's lineup so a person can choose from it.

    Returns {ok, channel_id, channel_title, entries}. Each entry carries
    the status Tuner already holds for it, so the page can show what is
    downloaded, queued or skipped rather than offering it blindly.
    """
    result = {"ok": False, "message": "", "channel_id": None,
              "channel_title": None, "entries": []}

    parsed = urls.parse(raw_url)
    if parsed["kind"] == urls.UNKNOWN:
        result["message"] = parsed.get("reason") or "not a YouTube address"
        return result

    try:
        resolved = ytdlp.resolve_channel(cfg, parsed)
        listing = ytdlp.list_channel(cfg, resolved["canonical_url"], limit=limit)
    except ytdlp.YtdlpError as exc:
        result["message"] = str(exc)
        return result

    channel_id = listing.get("channel_id") or resolved["channel_id"]
    known = db.get_channel_by_ytid(conn, channel_id)

    entries = []
    for entry in listing["entries"]:
        row = db.get_video_by_ytid(conn, entry["video_id"])
        entries.append({
            "video_id": entry["video_id"],
            "title": entry["title"],
            "duration": entry.get("duration"),
            "status": row["status"] if row is not None else None,
            "skip_reason": row["skip_reason"] if row is not None else None,
            "picked": bool(row["picked"]) if row is not None else False,
        })

    result.update({
        "ok": True,
        "channel_id": channel_id,
        "channel_title": (listing.get("channel_title") or resolved.get("title")
                          or channel_id),
        "tracked": known is not None,
        "subscribed": bool(known["enabled"]) if known is not None else False,
        "entries": entries,
        "message": "%d video%s listed" % (len(entries),
                                          "" if len(entries) == 1 else "s"),
    })
    return result


def pick(conn, cfg, raw_url, videos):
    """Queue a hand-chosen set of videos from a channel.

    The channel is created if it is not tracked, but left **unsubscribed**:
    holding a few curated videos is not the same as following everything a
    channel publishes. Subscribing is the enable toggle on its row.
    """
    result = {"ok": False, "message": "", "queued": 0, "channel": None}

    wanted = []
    for item in (videos or []):
        if isinstance(item, dict):
            video_id = str(item.get("video_id") or "").strip()
            title = str(item.get("title") or "").strip()
        else:
            video_id, title = str(item or "").strip(), ""
        if urls.VIDEO_ID_RE.match(video_id):
            wanted.append((video_id, title or "(fetching title)"))

    if not wanted:
        result["message"] = "nothing selected"
        return result

    parsed = urls.parse(raw_url)
    if parsed["kind"] == urls.UNKNOWN:
        result["message"] = parsed.get("reason") or "not a YouTube address"
        return result

    try:
        resolved = ytdlp.resolve_channel(cfg, parsed)
    except ytdlp.ResolveError as exc:
        result["message"] = "could not resolve: %s" % exc
        return result

    channel_id = resolved["channel_id"]
    channel = db.get_channel_by_ytid(conn, channel_id)
    if channel is None:
        title = resolved["title"] or channel_id
        row_id = db.add_channel(conn, channel_id, title,
                                resolved["canonical_url"],
                                paths.sanitize(title), enabled=False)
        channel = db.get_channel(conn, row_id)
        log.info(conn, "holding picks from %s (not subscribed)" % title,
                 channel_row_id=row_id)

    queued = 0
    for video_id, title in wanted:
        row = db.get_video_by_ytid(conn, video_id)
        if row is None:
            db.add_video(conn, video_id, channel["id"], title, status=db.NEW,
                         picked=True)
            queued += 1
        elif row["status"] in (db.DOWNLOADED, db.DOWNLOADING):
            continue          # already have it
        else:
            db.set_video_status(conn, row["id"], db.NEW, picked=1, attempts=0,
                                skip_reason=None, error=None)
            queued += 1

    log.info(conn, "%s: %d video%s picked by hand"
             % (channel["title"], queued, "" if queued == 1 else "s"),
             channel_row_id=channel["id"])

    result.update({"ok": True, "queued": queued, "channel": channel["title"],
                   "channel_row_id": channel["id"],
                   "message": "%d queued from %s" % (queued, channel["title"])})
    return result


def add_channel(conn, cfg, raw_url, seed_n=None, keep_n=None):
    """Add one channel by URL, then seed its newest videos.

    Returns {url, ok, message, channel}. Never raises: the add form shows
    one row per pasted URL and a bad line must not lose the good ones.
    """
    result = {"url": raw_url, "ok": False, "message": "", "channel": None}

    parsed = urls.parse(raw_url)
    if parsed["kind"] == urls.UNKNOWN:
        result["message"] = parsed.get("reason") or "not a YouTube address"
        return result

    try:
        resolved = ytdlp.resolve_channel(cfg, parsed)
    except ytdlp.ResolveError as exc:
        result["message"] = "could not resolve: %s" % exc
        return result

    channel_id = resolved["channel_id"]
    existing = db.get_channel_by_ytid(conn, channel_id)
    if existing is not None:
        result["message"] = "already tracked as %s" % existing["title"]
        result["channel"] = existing["title"]
        return result

    title = resolved["title"] or channel_id
    folder = paths.sanitize(title)

    row_id = db.add_channel(conn, channel_id, title, resolved["canonical_url"],
                            folder, seed_n=seed_n, keep_n=keep_n)
    if row_id is None:
        result["message"] = "already tracked"
        return result

    row = db.get_channel(conn, row_id)
    added = ingest(conn, cfg, row)
    row = db.get_channel(conn, row_id)          # ingest may fix the title
    counts = seed(conn, cfg, row_id)

    if row["last_error"]:
        result["message"] = "added, but the feed could not be read: %s" % (
            row["last_error"])
    elif added == 0:
        result["message"] = "added %s (no videos in the feed yet)" % row["title"]
    else:
        result["message"] = "added %s (%d queued, %d older skipped)" % (
            row["title"], counts["kept"], counts["skipped"])

    log.info(conn, "channel added: %s" % row["title"], channel_row_id=row_id)

    result["ok"] = True
    result["channel"] = row["title"]
    result["channel_row_id"] = row_id
    return result


def add_channels(conn, cfg, raw_urls, seed_n=None, keep_n=None):
    """Add many, reporting one result per line pasted."""
    seen = set()
    results = []
    delay = min(feed_delay(cfg), 1.0)   # a paste of forty should still finish
    for position, raw in enumerate(raw_urls):
        text = (raw or "").strip()
        if not text:
            continue
        if text in seen:
            results.append({"url": text, "ok": False, "channel": None,
                            "message": "duplicate line"})
            continue
        seen.add(text)
        # Each add is a yt-dlp resolve plus a feed fetch. Firing forty of
        # those at once is what gets the whole batch throttled.
        if position:
            pause(delay)
        results.append(add_channel(conn, cfg, text, seed_n=seed_n,
                                   keep_n=keep_n))
    return results


def process_new(conn, cfg, limit=None):
    """Probe every newly discovered video and decide its fate.

    Returns counts by outcome. Nothing here downloads: accepted rows are
    handed to the queue and the worker takes it from there.
    """
    counts = {"queued": 0, "skipped": 0, "failed": 0, "retry": 0}
    max_attempts = max(1, int(cfg.get("max_attempts", 3) or 1))

    rows = db.list_videos(conn, status=db.NEW, limit=limit,
                          order="published_at ASC, id ASC")

    for row in rows:
        row_id = row["id"]
        channel = db.get_channel(conn, row["channel_row_id"])
        db.set_video_status(conn, row_id, db.PROBING)

        try:
            meta = ytdlp.probe(cfg, row["video_id"])
        except ytdlp.PermanentProbeError as exc:
            db.set_video_status(conn, row_id, db.SKIPPED,
                                skip_reason=exc.reason or "unavailable",
                                error=str(exc))
            log.info(conn, "skipped %s: %s" % (row["title"], exc.reason),
                     channel_row_id=row["channel_row_id"],
                     video_id=row["video_id"])
            counts["skipped"] += 1
            continue
        except ytdlp.TransientProbeError as exc:
            attempts = db.bump_attempts(conn, row_id)
            if attempts >= max_attempts:
                db.set_video_status(conn, row_id, db.FAILED, error=str(exc))
                log.warn(conn, "probe failed %d times, giving up on %s: %s"
                         % (attempts, row["title"], exc),
                         channel_row_id=row["channel_row_id"],
                         video_id=row["video_id"])
                counts["failed"] += 1
            else:
                db.set_video_status(conn, row_id, db.NEW, error=str(exc))
                log.warn(conn, "probe failed for %s (attempt %d): %s"
                         % (row["title"], attempts, exc),
                         channel_row_id=row["channel_row_id"],
                         video_id=row["video_id"])
                counts["retry"] += 1
            continue

        settings = filters.settings_for(cfg, channel)
        if row["picked"]:
            # Chosen by hand. The filters exist to decide what to take
            # automatically, and overruling a deliberate choice with them
            # would be perverse.
            action, reason = filters.ACCEPT, None
        else:
            action, reason = filters.decide(meta, settings)

        fields = {"duration_s": meta.get("duration"), "error": None}
        if meta.get("title"):
            fields["title"] = meta["title"]
        # A yt-dlp flat listing carries no date, so the filename would fall
        # back to today. The probe knows the real one.
        if not row["published_at"] and meta.get("upload_date"):
            stamp = str(meta["upload_date"])
            if len(stamp) == 8 and stamp.isdigit():
                fields["published_at"] = "%s-%s-%sT00:00:00Z" % (
                    stamp[:4], stamp[4:6], stamp[6:])

        if action == filters.SKIP:
            db.set_video_status(conn, row_id, db.SKIPPED, skip_reason=reason,
                                **fields)
            log.info(conn, "skipped %s: %s" % (meta.get("title") or row["title"],
                                               reason),
                     channel_row_id=row["channel_row_id"],
                     video_id=row["video_id"])
            counts["skipped"] += 1
        else:
            db.set_video_status(conn, row_id, db.QUEUED, skip_reason=None,
                                **fields)
            log.info(conn, "queued %s" % (meta.get("title") or row["title"]),
                     channel_row_id=row["channel_row_id"],
                     video_id=row["video_id"])
            counts["queued"] += 1

    return counts


def ingest_all(conn, cfg, only_channel_id=None, stop_event=None):
    """Ingest every enabled channel, spaced out. Returns new rows found."""
    rows = [row for row in db.list_channels(conn, enabled_only=True)
            if only_channel_id is None or row["id"] == only_channel_id]

    delay = feed_delay(cfg)
    total = 0
    for index, row in enumerate(rows):
        if stop_event is not None and stop_event.is_set():
            break
        # Jittered so a fixed cadence never looks machine-made either.
        if index and pause(delay + random.random() * delay * 0.5, stop_event):
            break
        try:
            total += ingest(conn, cfg, row)
        except Exception as exc:  # a bad channel must not stop the cycle
            log.error(conn, "ingest crashed for %s: %s" % (row["title"], exc),
                      channel_row_id=row["id"])
    return total
