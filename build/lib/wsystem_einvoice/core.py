"""Validate one invoice offline and normalise the result (adds rule pack, line numbers, file name)."""
from __future__ import annotations
import re
from lxml import etree

from . import __version__
from .rules import RULES, engine

_STEP = re.compile(r"Q\{([^}]*)\}([\w.-]+)(?:\[(\d+)\])?")


def _steps(path: str) -> list[str]:
    out, buf, depth = [], "", 0
    for ch in path:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "/" and depth == 0:
            if buf:
                out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def line_of(root, location: str | None) -> int | None:
    """Map the SVRL location (XPath 3 EQName steps) to a source line of the parsed XML; None if it can't be resolved."""
    if not location or root is None:
        return None
    node = None
    for i, st in enumerate(_steps(location)):
        if st.startswith("@"):
            break
        m = _STEP.fullmatch(st)
        if not m:
            break
        ns, name, idx = m.group(1), m.group(2), int(m.group(3) or 1)
        tag = f"{{{ns}}}{name}" if ns else name
        if i == 0:
            if root.tag != tag:
                return None
            node = root
            continue
        kids = [c for c in node if c.tag == tag]
        if len(kids) < idx:
            break
        node = kids[idx - 1]
    return getattr(node, "sourceline", None) if node is not None else None


def validate_bytes(data: bytes, rules: str = "auto") -> dict:
    E = engine()
    try:
        r = E.validate(data, rules)
    except E.InputError as e:
        return {"valid": False, "input_error": str(e), "error_count": 1, "warning_count": 0, "errors": [], "warnings": []}
    root = None
    if r.get("container") == "xml":
        try:
            root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
        except etree.XMLSyntaxError:
            root = None
    for i in r.get("errors", []) + r.get("warnings", []):
        ln = line_of(root, i.get("location"))
        if ln:
            i["line"] = ln
    r["rule_pack"] = RULES["pack"]
    r["rule_versions"] = {"EN16931": "CEN 1.3.16 (via KoSIT config 2026-08-31)", "XRechnung": "3.0.2 (KoSIT config 2026-08-31, Schematron 2.4.0)"}
    r["validator"] = {"name": "W System e-invoice validator", "edition": "free", "version": __version__, "offline": True}
    return r
