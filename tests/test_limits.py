"""0.1.1: the 0.3.155.1 API protections in the self-hosted validator: caps before parsing, rule engine in a killable worker
process with a timeout, all configurable by environment variable (same defaults as the W System API)."""
import os, pathlib, subprocess, sys, json
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FX = ROOT / "fixtures"
VALID = FX / "valid" / "ubl-xrechnung-wsystem.xml"


def run(*args, **env):
    e = dict(os.environ, **{k: str(v) for k, v in env.items()})
    p = subprocess.run([sys.executable, "-m", "wsystem_einvoice", "validate", "--format", "json", *args], cwd=ROOT, env=e,
                       capture_output=True, text=True, timeout=300)
    return p.returncode, json.loads(p.stdout) if p.stdout.strip().startswith("{") else p


def test_defaults_match_the_api():
    from wsystem_einvoice.rules import engine
    E = engine()
    from wsystem_einvoice.engine import einvoice_worker as W
    assert (E.MAX_BYTES, E.MAX_LINES, E.MAX_ELEMENTS) == (5 * 1024 * 1024, 2000, 60000)
    assert (W.TIMEOUT, W.BUDGET, W.WORKERS) == (20.0, 30.0, 1)


def test_worker_is_used_by_default_and_runs_the_vendored_module():
    from wsystem_einvoice.engine import einvoice_worker as W
    assert W._PKG == "wsystem_einvoice.engine" and W.use_worker()
    assert (pathlib.Path(W._PKG_ROOT) / "wsystem_einvoice" / "engine" / "einvoice_worker.py").is_file()


def test_valid_file_through_the_worker():
    code, rep = run(str(VALID))
    assert code == 0 and rep["files"][0]["valid"] is True


def test_line_cap_is_configurable():
    code, rep = run(str(VALID), EINVOICE_MAX_LINES=0)
    f = rep["files"][0]
    assert code == 1 and f["status"] == "not checked" and f["error_code"] == "too_many_lines" and f["limit"] == 0


def test_element_cap_is_configurable():
    code, rep = run(str(VALID), EINVOICE_MAX_ELEMENTS=10)
    f = rep["files"][0]
    assert f["error_code"] == "too_many_elements" and f["limit"] == 10


def test_byte_cap_is_configurable():
    code, rep = run(str(VALID), EINVOICE_MAX_BYTES=100)
    assert rep["files"][0]["error_code"] == "payload_too_large"


def _heavy(tmp_path, n=1500):
    import re
    x = VALID.read_text(encoding="utf-8")
    m = re.search(r"<cac:InvoiceLine>.*?</cac:InvoiceLine>", x, re.S)
    p = tmp_path / "heavy.xml"
    p.write_text(x.replace(m.group(0), m.group(0) * n), encoding="utf-8")  # duplicate line ids + wrong totals: slow, many errors
    return p


def test_timeout_kills_the_engine_and_the_next_file_still_works(tmp_path):
    heavy = _heavy(tmp_path)
    code, rep = run(str(heavy), str(VALID), EINVOICE_TIMEOUT_S="0.3")
    first, second = rep["files"]
    assert first["status"] == "not checked" and first["error_code"] == "validation_timeout", first
    assert second["valid"] is True  # a fresh worker was started after the kill


def test_in_process_mode_has_no_timeout(tmp_path):
    code, rep = run(str(VALID), EINVOICE_WORKER=0, EINVOICE_TIMEOUT_S="0.001")
    assert code == 0 and rep["files"][0]["valid"] is True
