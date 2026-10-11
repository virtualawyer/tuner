#!/usr/bin/env python3
"""Tuner test runner.

Discovers every tests/cases/test_*.py and runs it. Stands in for the Debian
box: no test may touch the network, the real yt-dlp binary, or a real
Jellyfin. Fakes live in tests/fixtures/ and tests/harness.py.

Usage: python3 tests/run.py [pattern]
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Silence the journald mirror in tuner.log before the package imports.
os.environ.setdefault("TUNER_LOG_QUIET", "1")
# A password in the developer's shell would lock every request test out.
os.environ.pop("TUNER_PASSWORD", None)

# The package under test and the harness both import as top-level modules.
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else "test_*.py"
    cases = os.path.join(HERE, "cases")
    suite = unittest.defaultTestLoader.discover(cases, pattern=pattern, top_level_dir=HERE)
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    print("\nOK -- %d passed, %d failed" % (total - failed, failed)
          if not failed else
          "\nFAIL -- %d passed, %d failed" % (total - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
