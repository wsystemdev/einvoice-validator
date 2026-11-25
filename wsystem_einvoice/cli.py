"""`wsv`: validate e-invoices offline.

    wsv validate [PATH|GLOB ...] [--rules auto|en16931|xrechnung] [--format text|json|github] [--fail-on-warnings]
                 [--summary FILE] [--json-out FILE]
    wsv fetch-rules [DEST] | wsv rules | wsv licenses | wsv version

Exit codes: 0 all files valid; 1 at least one invalid or unreadable invoice; 2 usage error / no files matched; 3 rule files missing.
"""
from __future__ import annotations
import argparse, glob, json, os, pathlib, sys

from . import __version__
from .rules import RULES, RulesMissing, installed, rules_root

DISCLAIMER = ("Technical check against the official EN 16931 / XRechnung rule sets. A technically valid file is not tax or legal advice. "
              "Peppol BIS 3 specific rules are not checked.")
EXTS = (".xml", ".pdf")


def _expand(patterns: list[str]) -> list[str]:
    seen, out = set(), []
    for raw in patterns:
        for p in [x.strip() for x in raw.replace(",", "\n").splitlines() if x.strip()]:
            if os.path.isdir(p):
                hits = [str(f) for f in sorted(pathlib.Path(p).rglob("*")) if f.suffix.lower() in EXTS and f.is_file()]
            elif any(c in p for c in "*?["):
                hits = sorted(h for h in glob.glob(p, recursive=True) if os.path.isfile(h))
            else:
                hits = [p]
            for h in hits:
                if h not in seen:
                    seen.add(h)
                    out.append(h)
    return out


def _gh_escape(s: str, prop: bool = False) -> str:
    s = s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if prop:
        s = s.replace(":", "%3A").replace(",", "%2C")
    return s


def _issue_text(i: dict) -> str:
    bts = i.get("business_terms") or []
    t = f"{i.get('rule_id', '?')}: {i.get('message', '').strip()}"
    if bts:
        t += f" [{', '.join(bts)}]"
    if i.get("fix"):
        t += f" Fix: {i['fix']}"
    return t


def _status(r: dict, fail_on_warnings: bool) -> str:
    if r.get("input_error") or r.get("read_error"):
        return "not checked"
    if not r["valid"]:
        return "invalid"
    if fail_on_warnings and r.get("warning_count"):
        return "warnings"
    return "valid"


def cmd_validate(a) -> int:
    if not installed():
        print(f"rule files not installed under {rules_root()} (use the Docker image or run scripts/fetch_rules.py)", file=sys.stderr)
        return 3
    from .core import validate_bytes
    files = _expand(a.paths or ["**/*.xml"])
    if not files:
        print("no files matched", file=sys.stderr)
        return 2
    results = []
    for f in files:
        try:
            data = pathlib.Path(f).read_bytes()
        except OSError as e:
            r = {"valid": False, "read_error": f"cannot read file: {e.strerror}", "error_count": 1, "warning_count": 0, "errors": [], "warnings": []}
        else:
            try:
                r = validate_bytes(data, a.rules)
            except RulesMissing as e:
                print(str(e), file=sys.stderr)
                return 3
        r["file"] = f
        r["status"] = _status(r, a.fail_on_warnings)
        results.append(r)
        _emit(a.format, r)
    failed = sum(r["status"] != "valid" for r in results)
    report = {"validator": {"name": "W System e-invoice validator", "edition": "free", "version": __version__},
              "rule_pack": RULES["pack"], "checked": len(results), "failed": failed, "disclaimer": DISCLAIMER,
              "files": results}
    if a.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"\n{len(results)} file(s) checked, {failed} failed. Rule pack {RULES['pack']} (EN 16931 + XRechnung 3.0). {DISCLAIMER}",
              file=sys.stderr if a.format == "github" else sys.stdout)
    if a.json_out:
        pathlib.Path(a.json_out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as fh:
            fh.write(_summary_md(results, failed))
    return 1 if failed else 0


def _emit(fmt: str, r: dict) -> None:
    f = r["file"]
    if fmt == "json":
        return
    if fmt == "github":
        if r.get("input_error") or r.get("read_error"):
            print(f"::error file={_gh_escape(f, True)},title=not an e-invoice::{_gh_escape(r.get('input_error') or r.get('read_error'))}")
            return
        for kind, lst in (("error", r["errors"]), ("warning", r["warnings"])):
            for i in lst:
                loc = f"file={_gh_escape(f, True)}" + (f",line={i['line']}" if i.get("line") else "")
                print(f"::{kind} {loc},title={_gh_escape(i.get('rule_id', 'rule'), True)}::{_gh_escape(_issue_text(i))}")
        return
    if r.get("input_error") or r.get("read_error"):
        print(f"NOT CHECKED  {f}: {r.get('input_error') or r.get('read_error')}")
        return
    head = "VALID  " if r["valid"] else "INVALID"
    print(f"{head}  {f}  ({r.get('profile', '?')}, {r.get('syntax', '?').upper()}, {r['error_count']} errors, {r['warning_count']} warnings)")
    for kind, lst in (("error", r["errors"]), ("warning", r["warnings"])):
        for i in lst:
            ln = f" line {i['line']}" if i.get("line") else ""
            print(f"  {kind}{ln}: {_issue_text(i)}")


def _md(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _summary_md(results: list[dict], failed: int) -> str:
    icon = {"valid": "✅ valid", "invalid": "❌ invalid", "warnings": "⚠️ warnings", "not checked": "❌ not checked"}
    rows = ["## E-invoice validation (W System, offline)", "",
            f"{len(results)} file(s) checked, **{failed} failed**. Rule pack `{RULES['pack']}`: EN 16931 (CEN 1.3.16) + XRechnung 3.0.2 (KoSIT 2026-08-31).", "",
            "| File | Result | Profile | Errors | Warnings | First issue |", "|---|---|---|---|---|---|"]
    for r in results:
        first = (r["errors"] or r["warnings"] or [{}])[0]
        fi = r.get("input_error") or r.get("read_error") or (f"{first.get('rule_id')}: {first.get('message', '')[:120]}" if first else "")
        rows.append(f"| `{_md(r['file'])}` | {icon[r['status']]} | {_md(r.get('profile', ''))} | {r['error_count']} | {r['warning_count']} | {_md(fi)} |")
    rows += ["", f"_{DISCLAIMER}_", ""]
    return "\n".join(rows) + "\n"


def cmd_rules(a) -> int:
    print(json.dumps({**RULES, "installed": installed(), "rules_dir": rules_root()}, indent=2))
    return 0


def cmd_licenses(a) -> int:
    d = pathlib.Path(os.environ.get("WSV_LICENSES_DIR", "/licenses"))
    if not d.is_dir():
        d = pathlib.Path(__file__).resolve().parent.parent / "licenses"
    for p in sorted(d.glob("*")):
        print(f"===== {p.name} =====")
        print(p.read_text(errors="replace"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wsv", description="W System e-invoice validator (free, offline): EN 16931 + XRechnung")
    ap.add_argument("--version", action="version", version=f"wsv {__version__} (rule pack {RULES['pack']})")
    sub = ap.add_subparsers(dest="cmd")
    v = sub.add_parser("validate", help="validate invoice files (XML or ZUGFeRD/Factur-X PDF)")
    v.add_argument("paths", nargs="*", help="files, directories or globs (default **/*.xml)")
    v.add_argument("--rules", default="auto", choices=["auto", "en16931", "xrechnung"])
    v.add_argument("--format", default="text", choices=["text", "json", "github"])
    v.add_argument("--fail-on-warnings", action="store_true")
    v.add_argument("--summary", help="append a Markdown summary to this file (e.g. $GITHUB_STEP_SUMMARY)")
    v.add_argument("--json-out", help="also write the full JSON report here")
    fr = sub.add_parser("fetch-rules", help="one-time download of the pinned official rule files (checksum-verified); not needed in the Docker image")
    fr.add_argument("dest", nargs="?", default=None, help="target dir (default $WSV_RULES_DIR or ./rules)")
    fr.add_argument("--zip", help="install from a local copy of the official zip (no network)")
    sub.add_parser("rules", help="show the installed rule pack")
    sub.add_parser("licenses", help="print licence and third-party notices")
    sub.add_parser("version")
    a = ap.parse_args(argv)
    if a.cmd == "validate":
        return cmd_validate(a)
    if a.cmd == "fetch-rules":
        from .fetch import fetch
        return fetch(a.dest or os.environ.get("WSV_RULES_DIR") or "rules", a.zip)
    if a.cmd == "rules":
        return cmd_rules(a)
    if a.cmd == "licenses":
        return cmd_licenses(a)
    if a.cmd == "version":
        print(f"wsv {__version__} (rule pack {RULES['pack']})")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
