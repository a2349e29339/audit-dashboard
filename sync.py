#!/usr/bin/env python3
"""Headless SimpleFIN sync — run on a schedule (see install_autosync.sh for a
macOS launchd setup) or manually:
    python3 sync.py [days]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import run_sync  # noqa: E402

days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
result = run_sync(days=days)
stamp = time.strftime("%Y-%m-%d %H:%M:%S")
if result.get("ok"):
    print(f"[{stamp}] synced {result['accounts']} accounts, {result['added']} new transactions"
          + (f" (warnings: {result['warnings']})" if result.get("warnings") else ""))
    sys.exit(0)
print(f"[{stamp}] sync failed: {result.get('error')}")
sys.exit(1)
