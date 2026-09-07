#!/usr/bin/env python3
"""
Uptime check for the Deal Finder webapp. Reads the target URL from
../monitor_config.json (update that file's target_url once the app is
hosted — see webapp/README.md), hits it, and exits non-zero with a message
on stderr if it's down or slow. Designed to be run by a scheduled task; a
non-zero exit is the signal to alert on, not the alert itself.

Usage:
    webapp/scripts/check_uptime.py [--timeout SECONDS]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "monitor_config.json"


def load_target() -> str:
    with open(CONFIG_PATH) as f:
        return json.load(f)["target_url"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    url = load_target()
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=args.timeout) as resp:
            elapsed = time.monotonic() - t0
            body = resp.read().decode("utf-8", "replace")
            if resp.status != 200:
                print(f"DOWN: {url} returned HTTP {resp.status} after {elapsed:.1f}s", file=sys.stderr)
                return 1
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                print(f"DOWN: {url} returned non-JSON body after {elapsed:.1f}s: {body[:200]!r}", file=sys.stderr)
                return 1
            if not data.get("ok"):
                print(f"DOWN: {url} responded but ok=false after {elapsed:.1f}s: {data}", file=sys.stderr)
                return 1
            print(f"UP: {url} responded in {elapsed:.1f}s")
            return 0
    except urllib.error.URLError as exc:
        elapsed = time.monotonic() - t0
        print(f"DOWN: {url} unreachable after {elapsed:.1f}s ({exc.reason})", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        print(f"DOWN: {url} check failed after {elapsed:.1f}s ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
