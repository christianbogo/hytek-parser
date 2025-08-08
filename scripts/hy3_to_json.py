#!/usr/bin/env python3
"""Convenience wrapper to run the HY3 -> JSON conversion.

This simply calls the library CLI entry point.
"""

from hytek_parser.cli import main


if __name__ == "__main__":
    raise SystemExit(main())


