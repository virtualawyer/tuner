"""The Jellyfin client.

The only module that knows Jellyfin's REST shape. Nothing here raises: a
media server that is down, misconfigured, or holding a stale key must never
take Tuner's poll loop with it.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import USER_AGENT

TIMEOUT = 15
PAGE_SIZE = 500
MAX_PAGES = 40

# Jellyfin reports container paths (/media/...) while Tuner writes host
# paths (/srv/media/...), so comparing paths never matches. Every file
# Tuner writes carries the YouTube id in its name, and that token is
# independent of how Jellyfin sees the filesystem.
ID_TOKEN_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\]")

# Tests replace this with a stub opener.
OPENER = None


def configured(cfg):
    """True when there is enough to try a call at all."""
    return bool((cfg.get("jellyfin_url") or "").strip()
                and (cfg.get("jellyfin_api_key") or "").strip())


def mask_key(key):
    """What the browser is allowed to see of the API key."""
    text = (key or "").strip()
    if not text:
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def base_url(cfg):
    return (cfg.get("jellyfin_url") or "").strip().rstrip("/")


def _open(request, timeout):
    if OPENER is not None:
        return OPENER(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def request(cfg, path, params=None, method="GET", timeout=TIMEOUT):
    """One call. Returns (status, payload, error) and never raises.

    `payload` is the decoded JSON when there is any, otherwise None.
    """
    root = base_url(cfg)
    if not root:
        return (0, None, "no Jellyfin URL configured")

    url = root + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)

    req = urllib.request.Request(url, method=method)
    req.add_header("X-Emby-Token", (cfg.get("jellyfin_api_key") or "").strip())
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    if method == "POST":
        req.add_header("Content-Length", "0")

    try:
        response = _open(req, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return (401, None, "Jellyfin rejected the API key")
        if exc.code == 404:
            return (404, None, "not found: %s" % path)
        return (exc.code, None, "Jellyfin returned HTTP %d" % exc.code)
    except urllib.error.URLError as exc:
        return (0, None, "cannot reach Jellyfin: %s" % exc.reason)
    except (OSError, ValueError) as exc:
        return (0, None, "cannot reach Jellyfin: %s" % exc)

    try:
        status = getattr(response, "status", None) or response.getcode()
        raw = response.read()
    except (OSError, ValueError) as exc:
        return (0, None, "cannot read the Jellyfin reply: %s" % exc)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    if not raw:
        return (status, None, None)

    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return (status, json.loads(raw), None)
    except ValueError:
        return (status, None, "Jellyfin sent something that is not JSON")


def system_info(cfg):
    """Connection test. Always returns a dict, never raises."""
    if not configured(cfg):
        return {"ok": False, "server_name": None, "version": None,
                "error": "set the Jellyfin URL and API key first"}

    status, payload, error = request(cfg, "/System/Info")
    if error:
        return {"ok": False, "server_name": None, "version": None,
                "error": error}
    if not isinstance(payload, dict):
        return {"ok": False, "server_name": None, "version": None,
                "error": "unexpected reply from Jellyfin"}

    return {
        "ok": True,
        "server_name": payload.get("ServerName"),
        "version": payload.get("Version"),
        "error": None,
    }


def users(cfg):
    """[{id, name}] for the settings picker. Empty on any failure."""
    if not configured(cfg):
        return []
    status, payload, error = request(cfg, "/Users")
    if error or not isinstance(payload, list):
        return []
    return [{"id": item.get("Id"), "name": item.get("Name")}
            for item in payload if isinstance(item, dict) and item.get("Id")]


def refresh_library(cfg, conn=None):
    """Ask Jellyfin to rescan. Failures become an event, never an exception.

    Tuner deletes its own files and then asks for a rescan rather than
    calling DELETE /Items, which would need delete rights on the API key.
    """
    from . import log

    if not configured(cfg):
        return False

    status, _, error = request(cfg, "/Library/Refresh", method="POST")
    if error:
        if conn is not None:
            log.warn(conn, "Jellyfin refresh failed: %s" % error)
        return False

    if conn is not None:
        log.info(conn, "asked Jellyfin to rescan the library")
    return True


def extract_video_id(item):
    """The YouTube id inside a Jellyfin item's path or name, or None."""
    for key in ("Path", "Name"):
        found = ID_TOKEN_RE.search(str(item.get(key) or ""))
        if found:
            return found.group(1)
    return None


def _played_page(cfg, user_id, start_index):
    """One page of played items, trying the modern route then the old one."""
    params = {
        "userId": user_id,
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Episode,Video",
        "Fields": "Path",
        "IsPlayed": "true",
        "EnableUserData": "true",
        "Limit": PAGE_SIZE,
        "StartIndex": start_index,
    }

    status, payload, error = request(cfg, "/Items", params=params)
    if status == 404:
        # Jellyfin before 10.9 only has the per-user route.
        params.pop("userId", None)
        status, payload, error = request(
            cfg, "/Users/%s/Items" % urllib.parse.quote(str(user_id)),
            params=params)

    if error:
        return (None, error)
    if not isinstance(payload, dict):
        return (None, "unexpected reply from Jellyfin")
    return (payload, None)


def played_video_ids(cfg):
    """{video_id: last played timestamp} for everything the user has watched.

    Items with no id token are ignored without comment: the library may
    hold plenty of media that has nothing to do with Tuner.
    """
    user_id = (cfg.get("jellyfin_user_id") or "").strip()
    if not configured(cfg) or not user_id:
        return {}

    found = {}
    start_index = 0
    for _ in range(MAX_PAGES):
        payload, error = _played_page(cfg, user_id, start_index)
        if error:
            return found

        items = payload.get("Items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            video_id = extract_video_id(item)
            if not video_id:
                continue
            user_data = item.get("UserData") or {}
            found[video_id] = user_data.get("LastPlayedDate")

        total = payload.get("TotalRecordCount")
        start_index += len(items)
        if not items or total is None or start_index >= int(total):
            break

    return found
