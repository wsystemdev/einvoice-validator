import json, pathlib, pytest
from wsystem_einvoice.core import validate_bytes

FX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
EXP = json.loads((FX / "expected.json").read_text())


@pytest.mark.parametrize("name", sorted(EXP["files"]))
def test_fixture(name):
    e = EXP["files"][name]
    r = validate_bytes((FX / name).read_bytes())
    if e.get("input_error"):
        assert r.get("input_error"), r
        return
    assert r["valid"] is e["valid"], (name, [x["rule_id"] for x in r["errors"]])
    assert sorted({x["rule_id"] for x in r["errors"]}) == e["errors"]
    assert r["profile"] == e["profile"] and r["syntax"] == e["syntax"]
    assert r["checked"]["PeppolBIS3"] is False
    assert r["rule_pack"] == EXP["rule_pack"]
    assert "not tax or legal advice" in r["disclaimer"]


def test_every_fixture_listed():
    on_disk = {str(p.relative_to(FX)) for p in FX.rglob("*") if p.suffix in (".xml", ".pdf")}
    assert on_disk == set(EXP["files"])
