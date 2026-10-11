"""Tuner -- a self-hosted YouTube subscription recorder for a Debian home server.

Tracks channels through their YouTube RSS feeds, downloads new videos with
yt-dlp into plain per-channel folders that Jellyfin serves, and prunes them
again on a keep-newest-N rule and on watched status read back from Jellyfin.

Standard library only. Runs on stock Debian python3.
"""

import threading

VERSION = "0.6.0"
PROJECT_URL = "https://github.com/virtualawyer/tuner"
# Says who is asking and where to read about it, on every feed request.
USER_AGENT = "Tuner/%s (+%s)" % (VERSION, PROJECT_URL)

# Set by anything that adds or removes a file in the library; read and
# cleared by the scheduler, which asks Jellyfin to rescan at most once per
# cycle. Lives here so the worker and retention never import the Jellyfin
# client, and so nothing has to import anything else to raise the flag.
LIBRARY_CHANGED = threading.Event()
