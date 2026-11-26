import json, pathlib, socket
import pytest
from wsystem_einvoice import cli, rules
from wsystem_einvoice.core import line_of

FX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_valid_exit_0(capsys):
    code, out, _ = run(capsys, "validate", str(FX / "valid"))
    assert code == 0 and "0 failed" in out and "not tax or legal advice" in out


def test_invalid_exit_1_github_annotations(capsys, tmp_path):
    s = tmp_path / "summary.md"
    code, out, _ = run(capsys, "validate", str(FX / "invalid" / "ubl-xrechnung-BR-DE-15-no-buyer-reference.xml"),
                       "--format", "github", "--summary", str(s))
    assert code == 1
    line = [l for l in out.splitlines() if l.startswith("::error")][0]
    assert "title=BR-DE-15" in line and ",line=" in line
    md = s.read_text()
    assert "| File | Result |" in md and "❌ invalid" in md and "BR-DE-15" in md


def test_not_an_invoice_annotated(capsys):
    code, out, _ = run(capsys, "validate", str(FX / "invalid" / "not-an-invoice.xml"), "--format", "github")
    assert code == 1 and "title=not an e-invoice" in out


def test_glob_comma_and_json(capsys, tmp_path):
    j = tmp_path / "r.json"
    code, out, _ = run(capsys, "validate", f"{FX}/valid/*.pdf, {FX}/valid/ubl-xrechnung-wsystem.xml", "--format", "json", "--json-out", str(j))
    rep = json.loads(out)
    assert code == 0 and rep["checked"] == 5 and rep["failed"] == 0
    assert json.loads(j.read_text())["rule_pack"] == "2026.11.1"


def test_fail_on_warnings(capsys):
    f = str(FX / "valid" / "KoSIT_01.01a-INVOICE_ubl.xml")  # valid, 1 warning (BR-DE-TMP-32)
    assert run(capsys, "validate", f)[0] == 0
    assert run(capsys, "validate", f, "--fail-on-warnings")[0] == 1


def test_no_match_exit_2(capsys, tmp_path):
    assert run(capsys, "validate", str(tmp_path / "*.xml"))[0] == 2


def test_missing_file_exit_1(capsys, tmp_path):
    code, out, _ = run(capsys, "validate", str(tmp_path / "nope.xml"))
    assert code == 1 and "NOT CHECKED" in out


def test_rules_missing_exit_3_and_no_download(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("WSV_RULES_DIR", str(tmp_path))
    code, _, err = run(capsys, "validate", str(FX / "valid"))
    assert code == 3 and "not installed" in err


def test_rules_and_licenses(capsys):
    code, out, _ = run(capsys, "rules")
    assert code == 0 and json.loads(out)["checks"]["PeppolBIS3"] is False
    code, out, _ = run(capsys, "licenses")
    assert code == 0 and "EUPL" in out and "Mozilla Public License" in out


def test_line_of_unresolvable():
    assert line_of(None, "/Q{x}a[1]") is None


def test_fetch_rules_from_local_zip(capsys, tmp_path, monkeypatch):
    import io, zipfile, hashlib
    from wsystem_einvoice import fetch as F
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("scenarios.xml", "<x/>")
    zp = tmp_path / "r.zip"
    zp.write_bytes(buf.getvalue())
    assert cli.main(["fetch-rules", str(tmp_path / "d"), "--zip", str(zp)]) == 1  # wrong checksum is refused
    comp = dict(F.RULES["components"][0], sha256=hashlib.sha256(buf.getvalue()).hexdigest())
    monkeypatch.setitem(F.RULES, "components", [comp] + F.RULES["components"][1:])
    assert cli.main(["fetch-rules", str(tmp_path / "d"), "--zip", str(zp)]) == 0
    assert (tmp_path / "d" / "2026-08-31" / ".ok").exists()
