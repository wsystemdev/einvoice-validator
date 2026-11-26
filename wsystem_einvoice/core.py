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


def _not_checked(msg: str, code: str, **extra) -> dict:
    return {"valid": False, "input_error": msg, "error_code": code, **extra, "error_count": 1, "warning_count": 0, "errors": [], "warnings": []}


def validate_bytes(data: bytes, rules: str = "auto") -> dict:
    """Validate one file. Same protections as the W System API (0.1.1): size / line / element caps before parsing
    (EINVOICE_MAX_BYTES / EINVOICE_MAX_LINES / EINVOICE_MAX_ELEMENTS) and the rule engine in a separate worker process
    that is killed after EINVOICE_TIMEOUT_S (per file budget EINVOICE_REQUEST_BUDGET_S). EINVOICE_WORKER=0 runs in-process
    without the timeout. A file that hits a limit is reported as not checked with an error_code."""
    E = engine()
    from .engine import einvoice_worker as W
    tok = W.begin_request()
    try:
        r = E.validate(data, rules)
    except E.TooLarge as e:
        return _not_checked(str(e), e.code, limit=e.limit, found=e.found)
    except E.InputError as e:
        return _not_checked(str(e), "input_error")
    except W.ValidationTimeout:
        return _not_checked(f"validation took longer than {W.TIMEOUT:g} s and was stopped (EINVOICE_TIMEOUT_S); very large or "
                            f"error-heavy invoices can hit it: fix the first errors or split the invoice", "validation_timeout")
    except (W.Busy, W.WorkerCrashed) as e:
        return _not_checked(f"the validation worker failed: {e}", "validator_busy")
    finally:
        W.end_request(tok)
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
