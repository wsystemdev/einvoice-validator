#!/usr/bin/env python3
"""Install the pinned official rule pack (build time only). Verifies the SHA-256 from wsystem_einvoice/RULES.json and
extracts the KoSIT release zip unmodified into <dest>/<dir>/, then writes the .ok marker the engine expects.

    python scripts/fetch_rules.py [dest]        (default: ./rules)
    python scripts/fetch_rules.py dest --zip local.zip   (no network)
"""
import argparse, hashlib, io, json, pathlib, shutil, sys, urllib.request, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULES = json.loads((ROOT / "wsystem_einvoice" / "RULES.json").read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", nargs="?", default=str(ROOT / "rules"))
    ap.add_argument("--zip")
    a = ap.parse_args()
    comp = RULES["components"][0]
    if a.zip:
        data = pathlib.Path(a.zip).read_bytes()
    else:
        req = urllib.request.Request(comp["url"], headers={"User-Agent": "wsystem-einvoice-validator-build"})
        data = urllib.request.urlopen(req, timeout=120).read()
    got = hashlib.sha256(data).hexdigest()
    if got != comp["sha256"]:
        print(f"checksum mismatch: {got}", file=sys.stderr)
        return 1
    out = pathlib.Path(a.dest) / RULES["engine_rules_dir_name"]
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
    (tmp / ".ok").write_text(comp["url"])
    (tmp / "RULES.json").write_text(json.dumps(RULES, indent=2))
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    print(f"installed rule pack {RULES['pack']} into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
