# Running Tuner in Docker

Step by step, for a home server where Jellyfin may already run in Docker.

One rule matters more than the rest, so it comes first:

> **Map `/config` to a normal Linux folder, never to the media drive.**
> The config volume holds the SQLite database. Media drives are often exFAT
> or FAT, which have no file locking, and SQLite corrupts on them. A folder
> such as `/opt/tuner` on the system disk is the right place. The container
> refuses to start if `/config` lands inside the library.

## Videos already in that folder

Anything already in the library folder is safe. Tuner only deletes files it
has a database row for, and it starts with none. Keep-N and watched deletion
both work off its own rows, so existing videos are never counted, pruned or
removed. Jellyfin's watched list is matched on the `[videoid]` Tuner puts in
every file name it writes, so files without one are ignored outright.

---

## Step 1 -- Find the library folder

Decide where videos go, and check the exact name, capitals included:

```bash
ls -d /srv/media/*outube*
```

The examples below use `/srv/media/YouTube`. Make the config folder too:

```bash
sudo mkdir -p /opt/tuner
```

## Step 2 -- Check free space for the image

Docker keeps images in `/var/lib/docker`, usually on the system disk rather
than the media drive.

```bash
df -h /
```

| Layer | Size on disk |
| --- | --- |
| `python:3.12-slim` base | ~175 MB |
| ffmpeg and its libraries | ~400 MB |
| yt-dlp (the release binary) | ~7 MB |
| **Image total** | **~585 MB** (about 210 MB to download) |

Measured on arm64. Building it yourself needs room for
the build cache as well, so leave about 1.5 GB free. If the system disk is
tighter than that, the systemd install in the
[README](README.md#install-without-docker) needs only ffmpeg from apt.

Docker's storage cannot move onto an exFAT drive: its overlay2 driver needs
a real Linux filesystem.

## Step 3 -- Start it

Debian's `docker.io` package ships **without** the compose plugin. Check:

```bash
docker compose version
```

If that says `'compose' is not a docker command`, use the plain Docker
commands. Everything works the same; compose is only a convenience.

### With compose

Edit the left side of the library line in
[`docker-compose.yml`](docker-compose.yml) to your folder, then:

```bash
sudo docker compose up -d
```

### Without compose

```bash
sudo docker run -d --name tuner --restart unless-stopped -p 8770:8770 --log-opt max-size=10m --log-opt max-file=3 -v /opt/tuner:/config -v /srv/media/YouTube:/media/youtube ghcr.io/virtualawyer/tuner:latest
```

Keep the two `-v` flags the right way round: the config volume must not land
inside the library.

### Building the image yourself

From a clone of this repository:

```bash
sudo docker build -t tuner .
sudo docker builder prune -f      # reclaim the build cache afterwards
```

Then use `tuner` in place of `ghcr.io/virtualawyer/tuner:latest`, or switch
the compose file from `image:` to `build: .`.

## Step 4 -- Open the firewall

```bash
sudo ufw allow 8770/tcp
```

## Step 5 -- Check it came up

```bash
sudo docker logs -f tuner
```

You want to see the yt-dlp version, the library path, and
`Tuner 0.6.0 listening on http://127.0.0.1:8770`. Then open
`http://<server-ip>:8770`.

The disk bar should show your media drive. If the header says "drive not
mounted", the host side of the library mount is wrong, or the drive is not
mounted on the host.

## Step 6 -- Add the library to Jellyfin

In Jellyfin: **Dashboard → Libraries → Add Media Library**, type **Movies**
(or **Other Videos**).

- **Folder: the path Jellyfin sees.** Two containers can reach the same
  folder by different paths. Tuner always calls it `/media/youtube` inside
  its own container. If Jellyfin maps `/srv/media` to `/media`, it sees the
  folder under its real name, `/media/YouTube`. Check with:

  ```bash
  docker exec jellyfin ls /media
  ```

- **Turn every metadata downloader off.** Otherwise Jellyfin matches YouTube
  videos against real films and gives them wrong titles and artwork. Tuner
  writes no `.nfo` files, so the file name is the metadata.
- Turn on **real time monitoring** so new files appear without a rescan.

## Step 7 -- Connect Jellyfin (only for delete-after-watched)

In Jellyfin: **Dashboard → API Keys → New API Key**. Copy it.

In Tuner: open **Settings**, paste the key and the server URL, press **Test
connection**, pick your user, then switch on **Delete videos once watched in
Jellyfin**.

For the URL, use the host's LAN address rather than `127.0.0.1`: inside a
container, localhost is the container itself.

```
http://<server-ip>:8096
```

## Step 8 -- Add your first channel

Paste a channel URL into the add field. Any of these work:

```
https://www.youtube.com/@veritasium
https://www.youtube.com/channel/UCHnyfMqiRRG1u-2MsSQLbXA
https://youtu.be/dQw4w9WgXcQ          (adds that video's channel)
@veritasium
```

Tuner grabs the three newest videos, then tracks the channel forward. Use
**Paste many** for a batch, one URL per line.

---

## Everyday commands

```bash
sudo docker logs -f tuner            # watch what it is doing
sudo docker restart tuner            # restart it
sudo docker stop tuner && sudo docker rm tuner    # stop and remove it
```

With compose: `sudo docker compose logs -f`, `restart`, `down`, `up -d`.

Your config, database and videos live in the mounted folders, so removing
the container loses nothing.

## Updating

```bash
sudo docker pull ghcr.io/virtualawyer/tuner:latest
sudo docker stop tuner && sudo docker rm tuner
```

Then run the `docker run` command from step 3 again. With compose:
`sudo docker compose pull && sudo docker compose up -d`.

## Updating yt-dlp

YouTube breaks yt-dlp regularly. This is the most common cause of downloads
suddenly failing.

The **Update yt-dlp** button in Settings works at once, but the change lives
inside the running container and is lost when it is recreated. Pulling a
newer image, or rebuilding your own with `--no-cache`, bakes in a fresh copy.
To pin a version when building:

```bash
sudo docker build --build-arg YTDLP_VERSION=2026.09.16 -t tuner .
```

## A password

Add `-e TUNER_PASSWORD=change-me` to `docker run` (or uncomment the lines in
the compose file) and every page asks for it. Any user name works. The
container healthcheck uses `/healthz`, which stays open.

## Troubleshooting

**The container exits at once with a message about exFAT.** `/config` is
mapped inside the library. Point it at `/opt/tuner` instead.

**"drive not mounted" in the header.** Check the host first:

```bash
findmnt /srv/media
```

If the drive is not mounted, `sudo mount -a`, then restart the container.
Docker will create an empty folder on a bare mount point if the drive is
missing when the container starts, so check the host before blaming Tuner.

**Downloads all fail at once.** yt-dlp is stale. See "Updating yt-dlp".

**Everything says "deferred".** Free space is below the floor. Lower keep-N
on your busiest channels, delete a few videos, or lower the floor in
Settings.

**Jellyfin will not connect.** Inside a container, `127.0.0.1` is the
container, not the host. Use the host's LAN address.

**Watched videos are not deleted.** All four must be true: a reachable
server, a valid key, a user selected, and the switch on. Matching works off
the `[videoid]` in each file name, so renaming files by hand breaks it.
