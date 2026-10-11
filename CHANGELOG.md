# Changelog

## 0.6.0 (2026-10-11): first public release

- **Other websites can no longer drive Tuner through your browser.** Every
  POST and PATCH must carry `Content-Type: application/json`, which makes a
  browser ask the server first, and Tuner never says yes to another site.
  Before this, a page on any site you visited could post text to
  `/api/settings` on your LAN and, for example, point the Jellyfin URL at a
  server it controlled, which would then receive your API key.
- Optional password: set `TUNER_PASSWORD` and the whole app asks for it
  (HTTP Basic, any user name). It is read from the environment only, so it
  never lands in `config.json` or the page.
- `GET /healthz` answers without a password, for container healthchecks.
- `config.json` stays owner-only (0600) when Settings saves it. A save used
  to rewrite it with the default umask, readable by every local user, and
  it holds the Jellyfin API key.
- The User-Agent now names the project page: `Tuner/0.6.0 (+https://github.com/virtualawyer/tuner)`.
- GitHub Actions: the tests on Python 3.9 to 3.13, and a multi-arch image
  (`linux/amd64`, `linux/arm64`) on GHCR for each release tag.
- Demo mode is read-only and never runs yt-dlp, as documented; it shows
  made-up channels.

## 0.5.0: browse a channel and pick videos by hand

- Paste a channel URL, list the newest 50 to 500 videos, tick the ones you
  want. Rows show what Tuner already holds, so nothing is picked twice.
- Picked videos ignore the Shorts and livestream filters and are never
  pruned by keep-N. A channel you only picked from is created unsubscribed.

## 0.4.3: ask for H.264 before AV1

- YouTube now serves AV1 as the best 1080p stream, which older clients
  cannot decode and Jellyfin then transcodes. The format chain asks for
  H.264 first at the same ceiling and falls back to any codec. The
  `prefer_h264` setting turns this off.

## 0.4.2: keep videos that actually downloaded

- A failed extra (usually a subtitle hitting HTTP 429) no longer throws
  away a video that merged fine. A half-merged file is still rejected.
- Premieres that have not started are skipped as upcoming instead of
  burning three attempts.
- Downloads pass `--retries 5 --fragment-retries 10 --sleep-requests 1`.

## 0.4.1: fall back to yt-dlp when the RSS feed is refused

- YouTube bot-mitigates `/feeds/videos.xml` per host. With `discovery` on
  `auto`, Tuner tries the feed and lists the channel through yt-dlp when the
  feed is refused. The log says which route ran.
- Publication dates come from `upload_date` on that path.

## 0.4.0: per-channel episode policy, and no bursts at YouTube

- `feed_delay_seconds` (default 3, jittered) between channels in a poll.
- `seed_n` per channel next to `keep_n`: download the last N, keep N.
  Raising it on a tracked channel backfills from the feed.

## 0.3.0: channels as a compact sortable list

- One row per channel with a filter box and sorting, controls opening in
  place, and status pills only for states that need action.

## 0.2.1: fetch a deleted or skipped video by hand

- **Download again** on deleted rows and **Download** on skipped ones.

## 0.2.0: thumbnails as Jellyfin posters

- A `.jpg` beside each video, which Jellyfin reads more reliably than art
  embedded in the MP4. New `thumbnails` setting, on by default.

## 0.1.1

- Fixed adding a channel by `@handle`.

## 0.1.0: first build

- Polls channel RSS feeds with conditional GET and jitter, probes new
  videos, skips Shorts and livestreams, downloads at a 1080p ceiling as MP4
  with English subtitles and embedded metadata, and files them in plain
  per-channel folders for Jellyfin.
- Keep-newest-N per channel, delete-after-watched through the Jellyfin API.
- Refuses to put SQLite on the media drive, deletes only inside the library,
  writes exFAT-safe names, stages downloads on the library filesystem, and
  pauses below a free-space floor.
- Systemd installer and a Docker image.
