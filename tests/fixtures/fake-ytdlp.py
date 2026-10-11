#!/usr/bin/env python3
"""A scriptable stand-in for the yt-dlp binary.

Tests point cfg["ytdlp_path"] at this file and set TUNER_FAKE_SCENARIO to a
JSON file describing what each invocation should do. No test ever runs the
real binary or touches the network.

Scenario shape:

  {
    "rules": [
      {
        "match": ["--version"],          # every substring must appear in argv
        "stdout": "2026.08.01",
        "stderr": "",
        "returncode": 0,
        "progress_lines": ["[download]   1.0% of 10.00MiB at 1.00MiB/s ETA 00:09"],
        "creates": ["%(id)s.mp4", "%(id)s.en.srt"],
        "creates_size": 2048
      }
    ],
    "default": {"returncode": 1, "stderr": "no scenario rule matched"}
  }

Rules are tried in order; the first whose substrings all appear wins.
"%(id)s" in a created filename is replaced with the video id parsed from the
watch URL. Files are created under the directory given by --paths home:<dir>.
"""

import json
import os
import re
import sys


def load_scenario():
    path = os.environ.get("TUNER_FAKE_SCENARIO")
    if not path or not os.path.exists(path):
        return {"rules": [], "default": {"returncode": 0, "stdout": ""}}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def pick_rule(scenario, argv_text):
    for rule in scenario.get("rules", []):
        needles = rule.get("match", [])
        if all(needle in argv_text for needle in needles):
            return rule
    return scenario.get("default", {"returncode": 1,
                                    "stderr": "no scenario rule matched"})


def home_dir(argv):
    for index, arg in enumerate(argv):
        if arg == "--paths" and index + 1 < len(argv):
            value = argv[index + 1]
            if value.startswith("home:"):
                return value[len("home:"):]
        if arg.startswith("--paths=home:"):
            return arg[len("--paths=home:"):]
    return None


def video_id(argv_text):
    found = re.search(r"watch\?v=([A-Za-z0-9_-]{4,})", argv_text)
    return found.group(1) if found else "unknown"


def main():
    argv = sys.argv[1:]
    argv_text = " ".join(argv)
    scenario = load_scenario()
    rule = pick_rule(scenario, argv_text)

    # Record the invocation so a test can assert on the arguments used.
    record = os.environ.get("TUNER_FAKE_CALLS")
    if record:
        with open(record, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(argv) + "\n")

    for line in rule.get("progress_lines", []):
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    stdout = rule.get("stdout")
    if stdout:
        sys.stdout.write(stdout if stdout.endswith("\n") else stdout + "\n")
        sys.stdout.flush()

    stderr = rule.get("stderr")
    if stderr:
        sys.stderr.write(stderr if stderr.endswith("\n") else stderr + "\n")
        sys.stderr.flush()

    creates = rule.get("creates", [])
    if creates:
        target = home_dir(argv)
        if target:
            os.makedirs(target, exist_ok=True)
            vid = video_id(argv_text)
            filler = b"0" * int(rule.get("creates_size", 1024))
            for name in creates:
                path = os.path.join(target, name.replace("%(id)s", vid))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as handle:
                    handle.write(filler)

    return int(rule.get("returncode", 0))


if __name__ == "__main__":
    sys.exit(main())
