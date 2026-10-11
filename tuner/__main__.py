"""Entrypoint: python3 -m tuner"""

import argparse
import sys

from . import VERSION, config


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tuner",
        description="Track YouTube channels and download new videos for Jellyfin.",
    )
    parser.add_argument("--config", dest="config_path", default=None,
                        help="path to config.json (default: next to the app)")
    parser.add_argument("--port", type=int, default=None,
                        help="override the configured listen port")
    parser.add_argument("--demo", action="store_true",
                        help="serve seeded sample data, touching no real files")
    parser.add_argument("--version", action="version", version="Tuner " + VERSION)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    try:
        cfg = config.load(args.config_path)
    except config.ConfigError as exc:
        sys.stderr.write("tuner: %s\n" % exc)
        return 2

    if args.port is not None:
        cfg["port"] = args.port
    if args.demo:
        cfg["demo"] = True

    # Imported here so --help and --version work before any of the runtime
    # modules load.
    from . import server
    return server.serve(cfg)


if __name__ == "__main__":
    sys.exit(main())
