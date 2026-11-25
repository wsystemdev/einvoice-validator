"""Locate the installed (baked) rule pack and load the engine strictly offline.

The engine never downloads anything at runtime here: if the rule files are missing, validation fails with a clear
error instead of reaching the network. Rules are installed at image build time by scripts/fetch_rules.py.
"""
from __future__ import annotations
import json, os, pathlib

PKG = pathlib.Path(__file__).resolve().parent
RULES = json.loads((PKG / "RULES.json").read_text())
DEFAULT_DIRS = ("/opt/wsystem/rules", str(PKG.parent / "rules"))


class RulesMissing(RuntimeError):
    pass


def rules_root() -> str:
    """Directory that contains <engine_rules_dir_name>/ with a .ok marker (the engine's EINVOICE_ARTIFACTS)."""
    cands = [os.environ["WSV_RULES_DIR"]] if os.environ.get("WSV_RULES_DIR") else list(DEFAULT_DIRS)
    for c in cands:
        if os.path.exists(os.path.join(c, RULES["engine_rules_dir_name"], ".ok")):
            return c
    return cands[0]


_engine = None


def engine():
    """Import the vendored engine with EINVOICE_ARTIFACTS pointing at the baked rules and downloads disabled."""
    global _engine
    if _engine is None:
        os.environ["EINVOICE_ARTIFACTS"] = rules_root()
        from .engine import einvoice as E
        if E.XR_VERSION != RULES["engine_rules_dir_name"]:
            raise RulesMissing(f"engine expects rules {E.XR_VERSION}, pack has {RULES['engine_rules_dir_name']}")

        def _no_download():
            raise E.RulesUnavailable("rule files are not installed (offline build): run `python scripts/fetch_rules.py` "
                                     "or use the ghcr.io/wsystemdev/einvoice-validator image")
        E._download_artifact = _no_download
        E.XR_RETRIES = 0
        _engine = E
    return _engine


def installed() -> bool:
    return os.path.exists(os.path.join(rules_root(), RULES["engine_rules_dir_name"], ".ok"))
