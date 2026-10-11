"""Free space on the library filesystem.

The target box has a 13 GB root that has already hit 100% once, and a 2.8 TB
media drive that can still fill. Every download checks here first.
"""

import os

GB = 1024 ** 3


def usage(path):
    """Space figures for the filesystem holding `path`.

    A path that does not exist returns zeros with ok=False rather than
    raising, so the page still renders when the drive has not mounted.
    """
    try:
        stat = os.statvfs(path)
    except (OSError, ValueError):
        return {"total": 0, "used": 0, "free": 0, "percent": 0, "ok": False}

    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bavail * stat.f_frsize
    used = total - (stat.f_bfree * stat.f_frsize)
    percent = int(round((used * 100.0) / total)) if total else 0
    return {"total": total, "used": used, "free": free,
            "percent": percent, "ok": True}


def free_bytes(cfg):
    return usage(cfg.get("library_root", "")).get("free", 0)


def min_free_bytes(cfg):
    try:
        return max(0, int(cfg.get("min_free_gb", 10) or 0)) * GB
    except (TypeError, ValueError):
        return 10 * GB


def has_headroom(cfg):
    """True when there is room to start another download.

    An unreachable library path returns False on purpose: the worker should
    defer rather than write into a bare mount point.
    """
    report = usage(cfg.get("library_root", ""))
    if not report["ok"]:
        return False
    return report["free"] > min_free_bytes(cfg)


def human(size):
    """Bytes as a short human string, for log lines and events."""
    try:
        value = float(size or 0)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return "%d B" % int(value)
            return "%.1f %s" % (value, unit)
        value /= 1024.0
    return "%.1f TB" % value
