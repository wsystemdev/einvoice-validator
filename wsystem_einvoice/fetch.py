"""One-time, explicit install of the pinned official rule pack (the only code that uses the network; never called by `validate`).

Verifies the SHA-256 pinned in RULES.json and extracts the KoSIT release zip unmodified into <dest>/<dir>/ with the .ok marker.
"""
from __future__ import annotations
import hashlib, io, json, pathlib, shutil, sys, urllib.request, zipfile

from .rules import RULES


def fetch(dest: str, zip_path: str | None = None) -> int:
    comp = RULES["components"][0]
    if zip_path:
        data = pathlib.Path(zip_path).read_bytes()
    else:
        req = urllib.request.Request(comp["url"], headers={"User-Agent": "wsystem-einvoice-validator"})
        data = urllib.request.urlopen(req, timeout=120).read()
    got = hashlib.sha256(data).hexdigest()
    if got != comp["sha256"]:
        print(f"checksum mismatch: {got}", file=sys.stderr)
        return 1
    out = pathlib.Path(dest) / RULES["engine_rules_dir_name"]
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
    (tmp / ".ok").write_text(comp["url"])
    (tmp / "RULES.json").write_text(json.dumps(RULES, indent=2))
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    print(f"installed rule pack {RULES['pack']} into {out}")
    return 0
