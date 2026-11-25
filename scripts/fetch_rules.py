#!/usr/bin/env python3
"""Build-time rule install: python scripts/fetch_rules.py [dest] [--zip local.zip]  (same as `wsv fetch-rules`)."""
import argparse, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from wsystem_einvoice.fetch import fetch  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", nargs="?", default=str(ROOT / "rules"))
    ap.add_argument("--zip")
    a = ap.parse_args()
    sys.exit(fetch(a.dest, a.zip))
