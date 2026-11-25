import hashlib, json, pathlib

ENG = pathlib.Path(__file__).resolve().parent.parent / "wsystem_einvoice" / "engine"


def test_vendored_engine_unmodified():
    want = json.loads((ENG / "ENGINE_SHA256.json").read_text())
    assert want, "empty manifest"
    for f, h in want.items():
        assert hashlib.sha256((ENG / f).read_bytes()).hexdigest() == h, f"{f} edited: re-run scripts/sync_engine.py"
    assert len((ENG / "ENGINE_COMMIT").read_text().strip()) == 40
