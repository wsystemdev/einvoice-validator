#!/usr/bin/env python3
"""Copy the W System validation engine from the vatcheck main branch at a pinned commit, unmodified.

    python scripts/sync_engine.py /path/to/vatcheck-checkout <commit>

Writes wsystem_einvoice/engine/*.py and wsystem_einvoice/engine/ENGINE_COMMIT. tests/test_engine_sync.py
checks the copy against ENGINE_SHA256.json, so edits to the vendored files are caught.
"""
import hashlib, json, pathlib, subprocess, sys

FILES = ["einvoice.py", "einvoice_sk.py", "einvoice_no.py", "einvoice_explain.py", "einvoice_de.py"]
DEST = pathlib.Path(__file__).resolve().parent.parent / "wsystem_einvoice" / "engine"


def main(repo: str, commit: str) -> None:
    hashes = {}
    for f in FILES:
        data = subprocess.run(["git", "-C", repo, "show", f"{commit}:vatcheck/{f}"], check=True, capture_output=True).stdout
        (DEST / f).write_bytes(data)
        hashes[f] = hashlib.sha256(data).hexdigest()
    full = subprocess.run(["git", "-C", repo, "rev-parse", commit], check=True, capture_output=True, text=True).stdout.strip()
    (DEST / "ENGINE_COMMIT").write_text(full + "\n")
    (DEST / "ENGINE_SHA256.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")
    print(f"synced {len(FILES)} files from {full[:12]}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "main")
