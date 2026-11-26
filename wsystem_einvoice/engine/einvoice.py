"""EU e-invoice validation (EN 16931, XRechnung) and creation (UBL 2.1; CII and ZUGFeRD/Factur-X PDF via einvoice_cii / einvoice_pdf).

Rule artefacts are NOT bundled in this repo. They are downloaded once from the official
KoSIT release (Apache-2.0; it ships the CEN EN 16931 XSLT under EUPL-1.2, unmodified):
  https://github.com/itplr-kosit/validator-configuration-xrechnung
and cached in $EINVOICE_ARTIFACTS (default /tmp/einvoice-artifacts).

Peppol BIS 3 rules are deliberately NOT run: OpenPeppol has not given a licence that
allows use in a paid hosted service (see /workspace/growth/EINVOICE_LICENCES.md).
"""
from __future__ import annotations
import io, os, re, threading, zipfile, hashlib, time
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

import httpx
from lxml import etree

from . import einvoice_sk, einvoice_no

XR_VERSION = "2026-08-31"
XR_URL = ("https://github.com/itplr-kosit/validator-configuration-xrechnung/releases/download/"
          f"v{XR_VERSION}/xrechnung-3.0.2-validator-configuration-{XR_VERSION}.zip")
# Pinned checksum of the official release asset (env override for a new XR_VERSION).
XR_SHA256 = os.getenv("EINVOICE_XR_SHA256", "2530cd107c414511c5d0462ec10f886910395abfca820db82e83d70bf01221a8")
XR_RETRIES = int(os.getenv("EINVOICE_XR_RETRIES", 3))
XR_COOLDOWN = float(os.getenv("EINVOICE_XR_COOLDOWN", 30))
ART_DIR = os.getenv("EINVOICE_ARTIFACTS", "/tmp/einvoice-artifacts")
MAX_BYTES = int(os.getenv("EINVOICE_MAX_BYTES", 5 * 1024 * 1024))

NS = {
    "ubl_inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "ubl_cn": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
    "cii": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "svrl": "http://purl.oclc.org/dsdl/svrl",
}
XRECHNUNG_ID = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
XRECHNUNG_EXT_ID = XRECHNUNG_ID + "#conformant#urn:xeinkauf.de:kosit:extension:xrechnung_3.0"
# Severity overrides of the official KoSIT scenario "EN16931 XRechnung Extension" (scenarios.xml <customLevel>,
# config 3.0.2 / 2026-08-31). KoSIT applies them only when the CustomizationID is exactly XRECHNUNG_EXT_ID, e.g. to
# allow DIGA scheme IDs (XR01, XR03, ...) under BR-DEX-04..08 and third-party payments (BR-DEX-09).
# "information" is reported here as a warning with a profile_note; tests/test_einvoice_corpus.py checks this
# table against the downloaded scenarios.xml.
XRECHNUNG_EXT_LEVELS = {
    "cii": {"BR-CL-23": "warning", "BR-CL-21": "information", "BR-CL-11": "information", "BR-CL-10": "information",
            "BR-CL-24": "information", "BR-CL-25": "information", "BR-CL-26": "information",
            "CII-SR-452": "error", "CII-SR-453": "error", "CII-SR-454": "error", "CII-SR-465": "error", "CII-SR-466": "error",
            "CII-SR-475": "information", "CII-SR-476": "information"},
    "ubl": {"BR-CL-21": "information", "BR-CL-23": "warning", "BR-CL-24": "information", "BR-CL-10": "information",
            "BR-CL-11": "information", "BR-CL-25": "information", "BR-CL-26": "information", "BR-CO-16": "information",
            "UBL-CR-470": "information", "UBL-CR-646": "information"},
}
# 0.3.157.1: the same severity overrides for the official KoSIT scenarios "EN16931 XRechnung" (standard) and "EN16931 XRechnung CVD"
# (config 3.0.2 / 2026-08-31). KoSIT selects the scenario from the exact CustomizationID: standard XRECHNUNG_ID or XRECHNUNG_CVD_ID.
# Levels are per document type because the scenarios differ (UBL-CR-646 applies to UBL Invoice and, for CVD, CreditNote only).
# An "error" level can also escalate a rule that the Schematron reports as a warning. tests/test_einvoice_corpus.py checks
# these tables against the downloaded scenarios.xml.
XRECHNUNG_CVD_ID = XRECHNUNG_ID + "#compliant#urn:xeinkauf.de:kosit:xrechnung:cvd_0.9"
_CII_STD = {"BR-CL-23": "warning", "BR-CL-21": "warning",
            "CII-SR-452": "error", "CII-SR-453": "error", "CII-SR-454": "error", "CII-SR-465": "error", "CII-SR-466": "error",
            "CII-SR-475": "information", "CII-SR-476": "information"}
XRECHNUNG_STD_LEVELS = {
    "ubl_inv": {"BR-CL-23": "warning", "BR-CL-21": "warning", "UBL-CR-646": "error"},
    "ubl_cn": {"BR-CL-23": "warning", "BR-CL-21": "warning"},
    "cii": dict(_CII_STD),
}
XRECHNUNG_CVD_LEVELS = {
    "ubl_inv": {"BR-CL-13": "information", "BR-CL-23": "warning", "BR-CL-21": "warning", "UBL-CR-646": "error"},
    "ubl_cn": {"BR-CL-13": "information", "BR-CL-23": "warning", "BR-CL-21": "warning", "UBL-CR-646": "error"},
    "cii": {"BR-CL-13": "information", **_CII_STD},
}
PEPPOL_ID = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
EN16931_ID = "urn:cen.eu:en16931:2017"
DISCLAIMER = ("Technical check against the official EN 16931 / XRechnung rule sets. A technically valid file is not tax or legal advice. "
              "Peppol BIS 3 specific rules (PEPPOL-EN16931-*) are not checked yet.")
SOURCES = {"EN16931": "CEN/TC 434 EN 16931 validation artefacts (EUPL-1.2) https://github.com/ConnectingEurope/eInvoicing-EN16931",
           "XRechnung": "KoSIT XRechnung validator configuration (Apache-2.0) https://github.com/itplr-kosit/validator-configuration-xrechnung"}

_lock = threading.Lock()
_state: dict = {}


class InputError(ValueError):
    pass


# ---------------------------------------------------------------- artefacts
def _root() -> str:
    return os.path.join(ART_DIR, XR_VERSION)


class RulesUnavailable(RuntimeError):
    """The official rule artefact could not be fetched (upstream outage). Callers answer 503."""


def _download_artifact() -> bytes:
    last = "unknown error"
    for attempt in range(max(1, XR_RETRIES)):
        if attempt:
            time.sleep(min(2 * attempt, 6))
        try:
            r = httpx.get(XR_URL, follow_redirects=True, timeout=60)
            data = r.content
            if r.status_code != 200:
                last = f"HTTP {r.status_code}"
                continue
            if XR_SHA256 and hashlib.sha256(data).hexdigest() != XR_SHA256:
                last = f"checksum mismatch ({len(data)} bytes)"
                continue
            if not zipfile.is_zipfile(io.BytesIO(data)):
                last = f"not a zip ({len(data)} bytes)"
                continue
            return data
        except httpx.HTTPError as e:
            last = type(e).__name__
    raise RulesUnavailable(f"official e-invoice rule files temporarily unavailable ({last}); please retry in a minute")


def ensure_artifacts() -> str:
    root = _root()
    if os.path.exists(os.path.join(root, ".ok")):
        return root
    with _lock:
        if os.path.exists(os.path.join(root, ".ok")):
            return root
        failed_at = _state.get("artifact_failed_at")
        if failed_at and time.monotonic() - failed_at < XR_COOLDOWN:
            raise RulesUnavailable(_state.get("artifact_error", "official e-invoice rule files temporarily unavailable; please retry in a minute"))
        try:
            data = _download_artifact()
        except RulesUnavailable as e:
            _state["artifact_failed_at"] = time.monotonic()
            _state["artifact_error"] = str(e)
            raise
        _state.pop("artifact_failed_at", None)
        os.makedirs(ART_DIR, exist_ok=True)
        tmp = root + f".tmp{os.getpid()}"
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
        open(os.path.join(tmp, ".ok"), "w").write(XR_URL)
        shutil.rmtree(root, ignore_errors=True)  # a half-extracted dir without .ok
        os.replace(tmp, root)
    return root


def _saxon():
    if "proc" not in _state:
        from saxonche import PySaxonProcessor
        _state["proc"] = PySaxonProcessor(license=False)
    return _state["proc"]


XSL = {("ubl", "EN16931"): "resources/ubl/2.1/xsl/EN16931-UBL-validation.xsl",
       ("cii", "EN16931"): "resources/cii/16b/xsl/EN16931-CII-validation.xsl",
       ("ubl", "XRechnung"): "resources/xrechnung/3.0.2/xsl/XRechnung-UBL-validation.xsl",
       ("cii", "XRechnung"): "resources/xrechnung/3.0.2/xsl/XRechnung-CII-validation.xsl"}
XSD = {"ubl_inv": "resources/ubl/2.1/xsd/maindoc/UBL-Invoice-2.1.xsd",
       "ubl_cn": "resources/ubl/2.1/xsd/maindoc/UBL-CreditNote-2.1.xsd",
       "cii": "resources/cii/16b/xsd/CrossIndustryInvoice_100pD16B.xsd"}


def _xslt(syntax, ruleset):
    key = ("xsl", syntax, ruleset)
    if key not in _state:
        p = _saxon().new_xslt30_processor()
        _state[key] = p.compile_stylesheet(stylesheet_file=os.path.join(ensure_artifacts(), XSL[(syntax, ruleset)]))
    return _state[key]


def _xsd(doctype):
    key = ("xsd", doctype)
    if key not in _state:
        _state[key] = etree.XMLSchema(etree.parse(os.path.join(ensure_artifacts(), XSD[doctype])))
    return _state[key]


def warm():
    try:
        with _lock_run:
            for k in XSL:
                _xslt(*k)
            for d in XSD:
                _xsd(d)
    except Exception:
        pass


_lock_run = threading.RLock()

# ---------------------------------------------------------------- input
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_blank_text=False)
PDF_XML_NAMES = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "zugferd_invoice.xml", "order-x.xml")


def extract_xml(data: bytes) -> tuple[bytes, dict]:
    """Return (xml_bytes, meta). Accepts raw XML or a PDF with an embedded e-invoice (ZUGFeRD / Factur-X)."""
    if not data:
        raise InputError("empty body: send the invoice XML or PDF as the request body")
    if len(data) > MAX_BYTES:
        raise InputError(f"file too large (max {MAX_BYTES // 1024 // 1024} MB)")
    if data.lstrip()[:5] == b"%PDF-":
        from pypdf import PdfReader
        try:
            r = PdfReader(io.BytesIO(data))
            files = {}
            for name, contents in (r.attachments or {}).items():
                files[name] = contents[0] if isinstance(contents, list) else contents
        except Exception as e:
            raise InputError(f"could not read PDF: {e}")
        if not files:
            raise InputError("PDF has no embedded files: this is not a ZUGFeRD/Factur-X/XRechnung hybrid invoice")
        pick = next((n for n in files if n.lower() in PDF_XML_NAMES), None) or next((n for n in files if n.lower().endswith(".xml")), None)
        if not pick:
            raise InputError(f"PDF has embedded files but none is XML: {sorted(files)}")
        return files[pick], {"container": "pdf", "embedded_file": pick}
    return data, {"container": "xml"}


def _parse(xml: bytes):
    if b"<!DOCTYPE" in xml[:2048] or b"<!ENTITY" in xml[:4096]:
        raise InputError("DOCTYPE/ENTITY declarations are not allowed in e-invoices")
    try:
        return etree.fromstring(xml, _PARSER)
    except etree.XMLSyntaxError as e:
        raise InputError(f"not well-formed XML: {e}")


def detect(root) -> dict:
    tag = etree.QName(root)
    if tag.namespace == NS["ubl_inv"] and tag.localname == "Invoice":
        syntax, doctype = "ubl", "ubl_inv"
    elif tag.namespace == NS["ubl_cn"] and tag.localname == "CreditNote":
        syntax, doctype = "ubl", "ubl_cn"
    elif tag.namespace == NS["cii"] and tag.localname == "CrossIndustryInvoice":
        syntax, doctype = "cii", "cii"
    else:
        raise InputError(f"unsupported root element {{{tag.namespace}}}{tag.localname}: expected UBL Invoice/CreditNote or UN/CEFACT CII")
    if syntax == "ubl":
        cid = root.findtext("cbc:CustomizationID", namespaces=NS) or ""
    else:
        cid = root.findtext("rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", namespaces=NS) or ""
    cid = cid.strip()
    c = cid.lower()
    if cid == XRECHNUNG_EXT_ID:
        profile = "XRechnung Extension"
    elif "xrechnung" in c:
        profile = "XRechnung"
    elif "peppol.eu:2017:poacc:billing" in c:
        profile = "Peppol BIS Billing 3.0"
    elif "factur-x.eu" in c or "zugferd" in c:
        profile = "Factur-X/ZUGFeRD " + (c.split(":")[-1].upper() if ":" in c else "")
    elif c.startswith("urn:cen.eu:en16931:2017"):
        profile = "EN 16931" if "#" not in c else "EN 16931 CIUS/extension"
    else:
        profile = "unknown"
    notes = []
    if "minimum" in c or "basicwl" in c:
        notes.append("Factur-X MINIMUM and BASIC WL are not EN 16931 compliant invoices; in DE/FR B2B they are not valid e-invoices on their own.")
    if profile == "XRechnung Extension":
        notes.append("XRechnung 3.0 Extension: checked with the EN 16931 and XRechnung rules using the severity levels of KoSIT's "
                     "official Extension scenario (e.g. DIGA scheme IDs such as XR01/XR03 under BR-CL-10/11/21/25/26 are allowed and "
                     "reported as warnings). The receiver must accept the Extension.")
    if "extended" in c:
        notes.append("Factur-X EXTENDED goes beyond EN 16931: the EN 16931 CII syntax restrictions (CII-SR-*, CII-DT-*, e.g. "
                     "'... should not be present') are reported as warnings, not errors, because EXTENDED allows those elements. "
                     "EN 16931 business rules (BR-*) still apply as errors. The Factur-X EXTENDED schematron itself is not run.")
    return {"syntax": syntax, "document_type": doctype, "customization_id": cid, "profile": profile, "notes": notes}


# ---------------------------------------------------------------- plain-English layer
FIX_HINTS = {
    "BR-01": "Add the specification identifier (UBL cbc:CustomizationID / CII GuidelineSpecifiedDocumentContextParameter).",
    "BR-02": "Add an invoice number.",
    "BR-03": "Add the invoice issue date (YYYY-MM-DD).",
    "BR-05": "Add the invoice currency code (e.g. EUR).",
    "BR-06": "Add the seller's legal name.",
    "BR-07": "Add the buyer's name.",
    "BR-08": "Add the seller's postal address.",
    "BR-09": "Add the seller's country code (ISO 3166-1 alpha-2).",
    "BR-10": "Add the buyer's postal address.",
    "BR-11": "Add the buyer's country code.",
    "BR-16": "Add at least one invoice line.",
    "BR-CO-10": "Sum of line net amounts must equal the document line total: recompute LineExtensionAmount.",
    "BR-CO-13": "Total without VAT must be line total minus allowances plus charges.",
    "BR-CO-15": "Total with VAT must be total without VAT plus total VAT.",
    "BR-CO-16": "Amount due must be total with VAT minus prepaid amount plus rounding.",
    "BR-CO-17": "VAT amount per category must be taxable amount x rate, rounded to 2 decimals.",
    "BR-CO-26": "Give the seller a VAT ID, a legal registration ID or another identifier.",
    "BR-S-08": "VAT breakdown for standard rate must match the sum of the S-rated lines.",
    "BR-AE-02": "Reverse charge (AE) needs the seller's VAT ID and the buyer's VAT ID.",
    "BR-DE-1": "XRechnung needs payment instructions (payment means).",
    "BR-DE-2": "XRechnung needs a seller contact (name, phone and e-mail).",
    "BR-DE-15": "XRechnung needs a Buyer reference (Leitweg-ID for German public buyers).",
    "BR-DE-16": "XRechnung needs the seller VAT ID, tax number or tax representative.",
    "PEPPOL-EN16931-R020": "Add the seller electronic address (EndpointID with schemeID).",
}


# B68: code-list rules (BR-CL-*) carry no BT in their text; derive the business term from the SVRL location.
_COUNTRY_PARTY_BT = [  # (location substring, BT, party label); first match wins
    ("AccountingSupplierParty", "BT-40", "seller"), ("SellerTradeParty", "BT-40", "seller"),
    ("AccountingCustomerParty", "BT-55", "buyer"), ("BuyerTradeParty", "BT-55", "buyer"),
    ("SellerTaxRepresentativeTradeParty", "BT-69", "seller tax representative"),
    ("TaxRepresentativeParty", "BT-69", "seller tax representative"),
    ("OriginCountry", "BT-159", "item country of origin"),
    ("ShipToTradeParty", "BT-80", "deliver-to"), ("Delivery", "BT-80", "deliver-to"),
]


def _cl_terms(rid: str, loc: str) -> tuple[list[str], str | None]:
    """Business terms + fix hint for BR-CL-* code-list rules from the failing element's location."""
    loc = re.sub(r"\[[^\]]*\]", "", loc or "")
    if rid == "BR-CL-14":
        for key, bt, who in _COUNTRY_PARTY_BT:
            if key in loc:
                return [bt], (f"The {who} country code ({bt}) must be an ISO 3166-1 alpha-2 code such as DE, FR or AT, "
                              "not a country name like 'Deutschland' or 'Germany'. Map names to codes before writing the XML.")
        return [], "Country codes must be ISO 3166-1 alpha-2 (e.g. DE), not country names."
    if rid in ("BR-CL-03", "BR-CL-04", "BR-CL-05"):
        bt = "BT-6" if "TaxCurrencyCode" in loc else "BT-5"
        return [bt], "Use an ISO 4217 currency code such as EUR, not a symbol or a name like 'Euro'."
    if rid == "BR-CL-01":
        return ["BT-3"], "Use a UNTDID 1001 invoice type code such as 380 (invoice) or 381 (credit note)."
    if rid == "BR-CL-16":
        return ["BT-81"], "Use a UNTDID 4461 payment means code such as 58 (SEPA credit transfer) or 30 (credit transfer)."
    if rid in ("BR-CL-17", "BR-CL-18"):
        return [], "Use a UNCL 5305 VAT category code: S, Z, E, AE, K, G, O, L or M."
    if rid == "BR-CL-23":
        return ["BT-130"], "Use a UN/ECE Recommendation 20/21 unit code such as C62 (piece), HUR (hour) or DAY, not 'Stk' or 'pcs'."
    return [], None


def _clean(text: str, rid: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(r"^\[[^\]]+\]\s*-?\s*", "", t)
    return t


def _svrl(out: str, ruleset: str) -> list[dict]:
    doc = etree.fromstring(out.encode("utf-8"))
    res = []
    for fa in doc.iter("{%s}failed-assert" % NS["svrl"], "{%s}successful-report" % NS["svrl"]):
        rid = fa.get("id") or ""
        flag = (fa.get("flag") or fa.get("role") or "fatal").lower()
        text = "".join(fa.find("svrl:text", NS).itertext()) if fa.find("svrl:text", NS) is not None else ""
        bts = sorted(set(re.findall(r"\b(B[TG]-\d+)\b", text)))
        e = {"rule_id": rid, "severity": "warning" if flag in ("warning", "information", "info") else "error",
             "ruleset": ruleset, "message": _clean(text, rid), "location": fa.get("location"), "business_terms": bts}
        if rid in FIX_HINTS:
            e["fix"] = FIX_HINTS[rid]
        if rid.startswith("BR-CL-"):
            cl_bts, cl_fix = _cl_terms(rid, e["location"] or "")
            if cl_bts and not bts:
                e["business_terms"] = cl_bts
            if cl_fix and "fix" not in e:
                e["fix"] = cl_fix
        res.append(e)
    return res


def _xsd_errors(root, doctype) -> list[dict]:
    s = _xsd(doctype)
    if s.validate(etree.ElementTree(root)):
        return []
    out = []
    for e in list(s.error_log)[:50]:
        msg = re.sub(r"\{[^}]+\}", "", e.message)
        out.append({"rule_id": "XSD", "severity": "error", "ruleset": "XML Schema (UBL 2.1 / CII D16B)",
                    "message": f"Line {e.line}: {msg}", "location": e.path,
                    "fix": "The XML structure is wrong (unknown element, wrong order or bad value). Fix this first; business rules may be unreliable until it passes."})
    return out


def _extended_relax(i: dict) -> dict:
    """Factur-X/ZUGFeRD EXTENDED permits CII elements and repetitions that the EN 16931 CII syntax binding
    restricts (CII-SR-*, CII-DT-*). Downgrade those to warnings so EXTENDED files that validate with the
    profile's own schematron (e.g. Mustang) aren't reported invalid. BR-* business rules are left as is."""
    rid = i.get("rule_id") or ""
    if i.get("ruleset") == "EN16931" and i.get("severity") == "error" and (rid.startswith("CII-SR-") or rid.startswith("CII-DT-")):
        i = dict(i, severity="warning", profile_note="Allowed in Factur-X/ZUGFeRD EXTENDED; an error only for EN 16931 (COMFORT) and stricter profiles.")
    return i


def _xrechnung_ext_level(i: dict, levels: dict, scenario: str = "Extension") -> dict:
    """Apply the KoSIT scenario severity (customLevel) for one EN16931/XRechnung schematron issue.
    scenario names the KoSIT scenario for the note: "Extension" (default), "XRechnung" or "XRechnung CVD"."""
    lvl = levels.get(i.get("rule_id") or "")
    if not lvl or i.get("ruleset") not in ("EN16931", "XRechnung"):
        return i
    sev = "error" if lvl == "error" else "warning"
    if sev == i.get("severity"):
        return i
    if scenario == "Extension":
        note = ("Allowed in XRechnung Extension (KoSIT Extension scenario level: " + lvl + "); an error for standard XRechnung / EN 16931."
                if sev == "warning" else "An error in XRechnung Extension (KoSIT Extension scenario level: error).")
    else:
        note = (f"Reported as {lvl} by the official KoSIT {scenario} scenario (customLevel), not as an error; the file is not rejected for it."
                if sev == "warning" else f"An error in the official KoSIT {scenario} scenario (customLevel: error).")
    return dict(i, severity=sev, profile_note=note)


def kosit_levels(d: dict):
    """(scenario name, severity table) of the official KoSIT scenario that matches this CustomizationID, or None."""
    cid = d["customization_id"]
    if d["syntax"] == "cii":
        dt = "cii"
    else:
        dt = d["document_type"]
    if d["profile"] == "XRechnung Extension":
        return "Extension", XRECHNUNG_EXT_LEVELS[d["syntax"]]
    if cid == XRECHNUNG_ID:
        return "XRechnung", XRECHNUNG_STD_LEVELS[dt]
    if cid == XRECHNUNG_CVD_ID:
        return "XRechnung CVD", XRECHNUNG_CVD_LEVELS[dt]
    return None


def validate(data: bytes, rules: str = "auto") -> dict:
    t0 = time.time()
    xml, meta = extract_xml(data)
    root = _parse(xml)
    d = detect(root)
    rules = (rules or "auto").lower()
    sets = ["EN16931"]
    if rules == "xrechnung" or (rules == "auto" and d["profile"] in ("XRechnung", "XRechnung Extension")):
        sets.append("XRechnung")
    elif rules not in ("auto", "en16931"):
        raise InputError("rules must be auto, en16931 or xrechnung (peppol is not available yet)")
    with _lock_run:
        issues = _xsd_errors(root, d["document_type"])
        node = _saxon().parse_xml(xml_text=etree.tostring(root, encoding="unicode"))
        for rs in sets:
            issues += _svrl(_xslt(d["syntax"], rs).transform_to_string(xdm_node=node), rs)
    if d["syntax"] == "cii" and "extended" in d["customization_id"].lower():
        issues = [_extended_relax(i) for i in issues]
    kl = kosit_levels(d) if "XRechnung" in sets else None
    if kl:
        issues = [_xrechnung_ext_level(i, kl[1], kl[0]) for i in issues]
    sk_issues, sk = einvoice_sk.check(root, d["syntax"], NS)
    issues += sk_issues
    no_issues, no = einvoice_no.check(root, d["syntax"], NS)
    issues += no_issues
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    from .einvoice_explain import annotate
    annotate(errors); annotate(warnings)  # plain-English what / field / fix next to the official rule ID (safe send)
    checked = {"xsd": True, "EN16931": True, "XRechnung": "XRechnung" in sets, "PeppolBIS3": False, "SK_national": sk["applies"], "NO_national": no["applies"]}
    notes = list(d["notes"]) + ([sk["note"]] if sk.get("note") else []) + ([no["note"]] if no.get("note") else [])
    out = {"valid": not errors, "error_count": len(errors), "warning_count": len(warnings),
           **meta, **{k: v for k, v in d.items() if k != "notes"}, "notes": notes,
           "checked": checked, "errors": errors, "warnings": warnings,
           "rule_versions": {"EN16931": "1.3.15+ via KoSIT config", "XRechnung": f"3.0.2 config {XR_VERSION}"},
           "sources": SOURCES, "disclaimer": DISCLAIMER, "ms": int((time.time() - t0) * 1000)}
    if sk["applies"]:
        out["rule_versions"]["SK_national"] = einvoice_sk.TRANSPOSITION
        out["slovakia"] = sk
    if no["applies"]:
        out["rule_versions"]["NO_national"] = einvoice_no.VERSION
        out["norway"] = no
    out.update(classify(errors))
    de = germany_hint(root, d, out)
    if de:
        fi = final_invoice_hint(root, d)
        if fi:
            de["final_invoice"] = fi
        out["germany"] = de
    fr = france_hint(root, d)
    if fr:
        out["france"] = fr
    return out


# ---------------------------------------------------------------- format vs business-rule split (B47)
BMF_SOURCE = "BMF letter of 15 Oct 2026 on B2B e-invoicing, Rn. 22-23 (see https://wsystem.dev/germany-bmf-letter-15-october-2026-e-invoicing)"


def classify(errors: list[dict]) -> dict:
    """Split errors into format errors (XML Schema: file can't be read as the structured format)
    and business-rule errors (schematron: EN 16931 / CIUS / national rules)."""
    fmt = [e for e in errors if e.get("rule_id") == "XSD"]
    br = [e for e in errors if e.get("rule_id") != "XSD"]
    for e in errors:
        e["error_class"] = "format" if e.get("rule_id") == "XSD" else "business_rule"
    return {"format_error_count": len(fmt), "business_rule_error_count": len(br)}


def _country(root, syntax: str, party: str) -> str:
    if syntax == "ubl":
        tag = "cac:AccountingSupplierParty" if party == "seller" else "cac:AccountingCustomerParty"
        v = root.findtext(f"{tag}/cac:Party/cac:PostalAddress/cac:Country/cbc:IdentificationCode", namespaces=NS)
    else:
        tag = "ram:SellerTradeParty" if party == "seller" else "ram:BuyerTradeParty"
        v = root.findtext(f"rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement/{tag}/ram:PostalTradeAddress/ram:CountryID", namespaces=NS)
    return (v or "").strip().upper()


def germany_hint(root, d: dict, out: dict) -> dict | None:
    """Plain-English hint on how the German VAT view (BMF letter) reads this result. Not tax advice."""
    seller, buyer = _country(root, d["syntax"], "seller"), _country(root, d["syntax"], "buyer")
    prof = d.get("profile") or ""
    if not (seller == "DE" or buyer == "DE" or prof.startswith("XRechnung") or "ZUGFeRD" in prof):
        return None
    from . import einvoice_de
    summ = einvoice_de.annotate(out.get("errors") or [])
    summ["vat_mandatory_warning_count"] = einvoice_de.annotate(out.get("warnings") or [])["vat_mandatory_error_count"]
    n, nu = summ["vat_mandatory_error_count"], summ["vat_mandatory_unconditional_error_count"]
    if out["format_error_count"]:
        status, text = "format_error", ("The file fails the XML Schema, so it can't be read as the structured format. Under the BMF letter "
                                        "a file with a format error is not an e-invoice for VAT purposes; fix the structure first.")
    elif out["business_rule_error_count"]:
        status = "business_rule_errors_only"
        text = ("No format error, only business-rule errors. Under the BMF letter such a file is in principle still an e-invoice "
                "for VAT purposes, and it is a content error only if a VAT-mandatory item (section 14(4) UStG) is affected. ")
        if nu:
            text += (("1 error touches" if nu == 1 else f"{nu} errors touch") + " a business term that the KoSIT/FeRD table maps "
                     "to a VAT-mandatory item (see de_vat_items on each error), so this is likely a content error in the VAT sense. ")
        elif n:
            text += (("1 error touches" if n == 1 else f"{n} errors touch") + " an item that is VAT-mandatory only in some cases (for example reverse charge "
                     "or a fiscal representative); check whether that case applies. ")
        else:
            text += "None of the errors names a business term mapped to a VAT-mandatory item. "
        text += ("Technically it is not a valid EN 16931 invoice, so the recipient's system may still reject it: fix the errors before sending.")
    else:
        status, text = "no_errors_found", "No format or business-rule errors found by the checks listed in 'checked'."
    note = ("Each error's business terms are matched against the KoSIT/FeRD mapping of the VAT-mandatory items (sections 14(4), 14a, 22c UStG). "
            "Errors whose rule text names no business term can't be matched; check those yourself. Not tax advice.")
    return {"applies": True, "seller_country": seller or None, "buyer_country": buyer or None, "de_vat_status": status,
            "explanation": text, "vat_mandatory_fields_checked": True,
            "vat_mandatory_affected": bool(nu) if status == "business_rule_errors_only" else None,
            **summ, "mapping": einvoice_de.VERSION, "mapping_source": einvoice_de.SOURCE,
            "note": note, "source": BMF_SOURCE}


FINAL_INVOICE_SOURCE = ("BMF letter of 15 Oct 2026 on B2B e-invoicing, Rn. 3-4 (see "
                        "https://wsystem.dev/germany-bmf-letter-15-october-2026-e-invoicing)")


def final_invoice_hint(root, d: dict) -> dict | None:
    """B208: a German invoice that deducts payments already received (BT-113 Paid amount > 0) reads as a final invoice
    (Endrechnung). Until 30 June 2030 the BMF letter allows the received payments as one sum in BT-113 with the details of the
    partial payments in BT-22 (Invoice note). Hint only; not tax advice."""
    if d["syntax"] == "ubl":
        raw = root.findtext("cac:LegalMonetaryTotal/cbc:PrepaidAmount", namespaces=NS)
        notes = [n.text or "" for n in root.findall("cbc:Note", namespaces=NS)]
    else:
        raw = root.findtext("rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement/"
                            "ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:TotalPrepaidAmount", namespaces=NS)
        notes = [n.text or "" for n in root.findall("rsm:ExchangedDocument/ram:IncludedNote/ram:Content", namespaces=NS)]
    try:
        paid = float((raw or "0").strip() or 0)
    except ValueError:
        return None
    if paid <= 0:
        return None
    has_note = any(n.strip() for n in notes)
    text = ("This invoice deducts payments already received (BT-113 Paid amount), so it reads as a final invoice. Until 30 June 2030 "
            "the BMF letter allows the received payments as one sum in BT-113 with the details of each partial payment in BT-22 "
            "(Invoice note), or the deduction in an embedded attachment that the structured part refers to. ")
    text += ("An invoice note (BT-22) is present: check that it lists each partial payment (date, amount, VAT)."
             if has_note else
             "No invoice note (BT-22) found: add one listing each partial payment, or an embedded attachment with the details.")
    return {"detected": True, "paid_amount": raw.strip(), "invoice_note_present": has_note, "explanation": text,
            "source": FINAL_INVOICE_SOURCE, "note": "Hint only, not tax advice."}


# ---------------------------------------------------------------- France 2026 reform hint (B49)
FR_SOURCE = ("impots.gouv.fr, 'Donnees de facture a transmettre a l'administration' (art. 41 septies D annexe IV CGI): SIREN of supplier "
             "and client expected from 1 Sep 2026; see https://wsystem.dev/france-e-invoicing-2027-sme-software")


def _party_ids(root, syntax: str, party: str) -> dict:
    if syntax == "ubl":
        base = ("cac:AccountingSupplierParty" if party == "seller" else "cac:AccountingCustomerParty") + "/cac:Party"
        legal = [(el.text or "").strip() for el in root.findall(f"{base}/cac:PartyLegalEntity/cbc:CompanyID[@schemeID='0002']", NS)]
        vat = [(el.text or "").strip() for el in root.findall(f"{base}/cac:PartyTaxScheme/cbc:CompanyID", NS)]
    else:
        base = ("rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement/"
                + ("ram:SellerTradeParty" if party == "seller" else "ram:BuyerTradeParty"))
        legal = [(el.text or "").strip() for el in root.findall(f"{base}/ram:SpecifiedLegalOrganization/ram:ID[@schemeID='0002']", NS)]
        vat = [(el.text or "").strip() for el in root.findall(f"{base}/ram:SpecifiedTaxRegistration/ram:ID[@schemeID='VA']", NS)]
    return {"siren": next((x for x in legal if x), None), "vat": next((x for x in vat if x), None)}


def _siren_from_vat(v):
    v = (v or "").replace(" ", "").upper()
    return v[4:] if re.fullmatch(r"FR[0-9A-Z]{2}\d{9}", v) else None


def france_hint(root, d: dict) -> dict | None:
    """Checks the SIREN items the French reform added (supplier + client SIREN as legal registration id, ICD 0002).
    Hint only: does not change `valid` and does not run the French XP Z12-012 rules. Not tax advice."""
    seller, buyer = _country(root, d["syntax"], "seller"), _country(root, d["syntax"], "buyer")
    if "FR" not in (seller, buyer):
        return None
    missing, found = [], {}
    for role, cc, bt in (("seller", seller, "BT-30"), ("buyer", buyer, "BT-47")):
        if cc != "FR":
            continue
        ids = _party_ids(root, d["syntax"], role)
        found[f"{role}_siren"] = ids["siren"]
        if not ids["siren"]:
            hint = _siren_from_vat(ids["vat"])
            missing.append({"item": f"{role}_siren", "field": f"{bt} with schemeID 0002 (SIRENE)",
                            "message": f"No SIREN found as the {role}'s legal registration identifier."
                                       + (f" The FR VAT number suggests SIREN {hint}." if hint else ""),
                            "suggested_value": hint})
    return {"applies": True, "domestic_b2b": seller == buyer == "FR", "seller_country": seller or None, "buyer_country": buyer or None,
            **found, "missing": missing,
            "not_checked": ["category of operation (goods / services / both)", "delivery address if different",
                            "option to pay VAT on debits", "French XP Z12-012 platform rules and lifecycle statuses"],
            "explanation": ("French platforms (plateformes agreees) reject invoices with missing or inconsistent mandatory data. "
                            "Since 1 Sep 2026 the supplier's and the client's SIREN are among the data transmitted to the tax "
                            "administration for domestic B2B. This hint checks only the SIREN items."),
            "source": FR_SOURCE}


# ---------------------------------------------------------------- create (JSON -> UBL)
D2 = Decimal("0.01")
EXEMPT_DEFAULTS = {"AE": ("VATEX-EU-AE", "Reverse charge"), "K": ("VATEX-EU-IC", "Intra-Community supply"),
                   "G": ("VATEX-EU-G", "Export outside the EU"), "O": ("VATEX-EU-O", "Not subject to VAT"),
                   "E": (None, "Exempt from VAT")}
# Peppol participant scheme by country, used when deriving an EndpointID from a VAT ID.
VAT_SCHEME = {"AT": "9915", "BE": "0208", "DE": "9930", "DK": "0184", "ES": "9920", "FR": "9957", "GR": "9933",
              "HU": "9910", "IE": "9935", "IT": "0211", "LU": "9938", "NL": "9944", "PL": "9945", "PT": "9946",
              "RO": "9947", "SE": "0007", "SI": "9949", "SK": "9950", "CZ": "9929", "EE": "9931", "FI": "0216",
              "HR": "9934", "LT": "9937", "LV": "9939", "MT": "9943", "BG": "9926", "CY": "9928", "NO": "0192"}


def _dec(v, field) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        raise InputError(f"{field} must be a number")


def _a(v: Decimal) -> str:
    return str(v.quantize(D2, ROUND_HALF_UP))


def _num(v: Decimal) -> str:
    s = format(v.normalize(), "f")
    return s if s not in ("-0",) else "0"


def _x(v) -> str:
    return escape(str(v))


_PID_RE = re.compile(r"(?:iso6523-actorid-upis::)?(\d{4}):([^\s:@]+)", re.I)
RETIRED_UNITS = {"PK": "XPK"}  # B230: UN/ECE Rec 20 retired PK (pack); Rec 21 XPK is the valid code (BR-CL-23)


def split_participant_id(s) -> tuple[str, str] | None:
    """B230: 'iso6523-actorid-upis::0208:0685660237' or '0208:0685660237' (Peppol Directory forms) -> (value, scheme)."""
    m = _PID_RE.fullmatch(str(s or "").strip())
    return (m.group(2), m.group(1)) if m else None


def _endpoint(p: dict, fmt: str, role: str):
    if p.get("endpoint_id"):
        sp = split_participant_id(p["endpoint_id"])
        if sp:
            if p.get("endpoint_scheme") and str(p["endpoint_scheme"]).strip() != sp[1]:
                raise InputError(f"{role}.endpoint_id carries scheme {sp[1]} but {role}.endpoint_scheme is {p['endpoint_scheme']}; "
                                 "send the Peppol id as endpoint_scheme + endpoint_id, or the full id alone")
            return sp
        return p["endpoint_id"], p.get("endpoint_scheme") or ("EM" if "@" in p["endpoint_id"] else "0088")
    vat = (p.get("vat_id") or "").replace(" ", "").upper()
    if fmt == "peppol-bis3":
        cc = vat[:2]
        if cc == "BE" and vat[2:].isdigit():
            return vat[2:], "0208"
        if cc == "SK" and re.fullmatch(r"\d{10}", vat[2:]):
            return vat[2:], "0245"  # Slovak Peppol id = 0245:DIC (Financial Administration guidance; ~92% of SK directory entries)
        num = vat[2:]
        # Nordic schemes take the national registry number, not the VAT number (mirrors einvoice_route candidates;
        # PEPPOL-COMMON-R043 checks 0192 = 9-digit org no.; directory entries are 0184:<8> / 0007:<10> / 0216:0037<8>)
        if cc == "NO" and re.fullmatch(r"\d{9}(MVA)?", num):
            return num[:9], "0192"
        if cc == "DK" and re.fullmatch(r"\d{8}", num):
            return num, "0184"
        if cc == "SE" and re.fullmatch(r"\d{12}", num):
            return num[:10], "0007"
        if cc == "FI" and re.fullmatch(r"\d{8}", num):
            return "0037" + num, "0216"
        if cc in VAT_SCHEME and vat:
            return vat, VAT_SCHEME[cc]
        raise InputError(f"{role}.endpoint_id and endpoint_scheme are required for Peppol (could not derive from VAT ID)")
    if p.get("email"):
        return p["email"], "EM"
    if vat:
        return None, None
    return None, None


def _party(p: dict, role: str, fmt: str, cur: str) -> str:
    for f in ("name", "country"):
        if not p.get(f):
            raise InputError(f"{role}.{f} is required")
    cc = p["country"].upper()
    eid, esch = _endpoint(p, fmt, role)
    parts = []
    if eid:
        parts.append(f'<cbc:EndpointID schemeID="{_x(esch)}">{_x(eid)}</cbc:EndpointID>')
    if p.get("trading_name"):
        parts.append(f"<cac:PartyName><cbc:Name>{_x(p['trading_name'])}</cbc:Name></cac:PartyName>")
    addr = "<cac:PostalAddress>"
    if p.get("street"):
        addr += f"<cbc:StreetName>{_x(p['street'])}</cbc:StreetName>"
    if p.get("city"):
        addr += f"<cbc:CityName>{_x(p['city'])}</cbc:CityName>"
    if p.get("postal_code"):
        addr += f"<cbc:PostalZone>{_x(p['postal_code'])}</cbc:PostalZone>"
    addr += f"<cac:Country><cbc:IdentificationCode>{_x(cc)}</cbc:IdentificationCode></cac:Country></cac:PostalAddress>"
    parts.append(addr)
    if p.get("vat_id"):
        parts.append(f"<cac:PartyTaxScheme><cbc:CompanyID>{_x(p['vat_id'].replace(' ', '').upper())}</cbc:CompanyID>"
                     "<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>")
    if (fmt == "peppol-bis3" and role == "seller" and cc == "NO" and not p.get("tax_number")
            and str(p.get("legal_form") or "").upper() in ("AS", "ASA", "NUF")):
        # NO-R-002: AS / ASA / NUF sellers show "Foretaksregisteret" (Peppol BIS 3 Norwegian rule)
        parts.append("<cac:PartyTaxScheme><cbc:CompanyID>Foretaksregisteret</cbc:CompanyID>"
                     "<cac:TaxScheme><cbc:ID>TAX</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>")
    if p.get("tax_number"):
        parts.append(f"<cac:PartyTaxScheme><cbc:CompanyID>{_x(p['tax_number'])}</cbc:CompanyID>"
                     "<cac:TaxScheme><cbc:ID>FC</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>")
    le = f"<cac:PartyLegalEntity><cbc:RegistrationName>{_x(p['name'])}</cbc:RegistrationName>"
    if (not p.get("company_id") and fmt == "peppol-bis3" and cc == "NO"
            and re.fullmatch(r"NO\d{9}(MVA)?", (p.get("vat_id") or "").replace(" ", "").upper())):
        p = {**p, "company_id": p["vat_id"].replace(" ", "").upper()[2:11], "company_id_scheme": "0192"}
    if p.get("company_id"):
        sch = f' schemeID="{_x(p["company_id_scheme"])}"' if p.get("company_id_scheme") else ""
        le += f"<cbc:CompanyID{sch}>{_x(p['company_id'])}</cbc:CompanyID>"
    if p.get("legal_form") and role == "seller":
        le += f"<cbc:CompanyLegalForm>{_x(p['legal_form'])}</cbc:CompanyLegalForm>"  # BT-33 (seller only in EN 16931)
    parts.append(le + "</cac:PartyLegalEntity>")
    if p.get("contact_name") or p.get("phone") or p.get("email"):
        c = "<cac:Contact>"
        if p.get("contact_name"):
            c += f"<cbc:Name>{_x(p['contact_name'])}</cbc:Name>"
        if p.get("phone"):
            c += f"<cbc:Telephone>{_x(p['phone'])}</cbc:Telephone>"
        if p.get("email"):
            c += f"<cbc:ElectronicMail>{_x(p['email'])}</cbc:ElectronicMail>"
        parts.append(c + "</cac:Contact>")
    return "<cac:Party>" + "".join(parts) + "</cac:Party>"


def _taxcat(cat: str, rate: Decimal, tag="cac:ClassifiedTaxCategory") -> str:
    s = f"<{tag}><cbc:ID>{_x(cat)}</cbc:ID>"
    if cat != "O":
        s += f"<cbc:Percent>{_num(rate)}</cbc:Percent>"
    return s + f"<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></{tag}>"


def _ubl_ac(a: dict, cur: str, doc: bool) -> str:
    """B252: one cac:AllowanceCharge (document level with its VAT category, or line level)."""
    s = f"<cac:AllowanceCharge><cbc:ChargeIndicator>{'true' if a['charge'] else 'false'}</cbc:ChargeIndicator>"
    if a["reason_code"]:
        s += f"<cbc:AllowanceChargeReasonCode>{_x(a['reason_code'])}</cbc:AllowanceChargeReasonCode>"
    if a["reason"]:
        s += f"<cbc:AllowanceChargeReason>{_x(a['reason'])}</cbc:AllowanceChargeReason>"
    if a["percent"] is not None:
        s += f"<cbc:MultiplierFactorNumeric>{_num(a['percent'])}</cbc:MultiplierFactorNumeric>"
    s += f"<cbc:Amount currencyID=\"{cur}\">{_a(a['amount'])}</cbc:Amount>"
    if a["base"] is not None:
        s += f"<cbc:BaseAmount currencyID=\"{cur}\">{_a(a['base'])}</cbc:BaseAmount>"
    if doc:
        s += _taxcat(a["cat"], a["rate"], "cac:TaxCategory")
    return s + "</cac:AllowanceCharge>"


def build_ubl(j: dict) -> tuple[str, dict]:
    if not isinstance(j, dict):
        raise InputError("body must be a JSON object")
    fmt = (j.get("format") or "en16931").lower()
    if fmt not in ("en16931", "xrechnung", "peppol-bis3"):
        raise InputError("format must be en16931, xrechnung, peppol-bis3 (UBL), cii, xrechnung-cii (CII) or zugferd / factur-x / zugferd-xrechnung (PDF), or ksef-fa3")
    is_cn = str(j.get("type_code", "380")) in ("381", "396", "81", "83", "532")
    for f in ("invoice_number", "issue_date", "seller", "buyer", "lines"):
        if not j.get(f):
            raise InputError(f"{f} is required")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(j["issue_date"])):
        raise InputError("issue_date must be YYYY-MM-DD")
    cur = (j.get("currency") or "EUR").upper()
    if not re.fullmatch(r"[A-Z]{3}", cur):
        raise InputError("currency must be an ISO 4217 code like EUR")
    lines_xml, groups, total = [], {}, Decimal(0)
    if not isinstance(j["lines"], list) or len(j["lines"]) > 500:
        raise InputError("lines must be a list of up to 500 items")
    for i, ln in enumerate(j["lines"], 1):
        if not isinstance(ln, dict) or not ln.get("description"):
            raise InputError(f"lines[{i-1}].description is required")
        q = _dec(ln.get("quantity", 1), f"lines[{i-1}].quantity")
        price = _dec(ln.get("unit_price"), f"lines[{i-1}].unit_price")
        if price < 0:
            raise InputError(f"lines[{i-1}].unit_price must not be negative (use a negative quantity)")
        cat = (ln.get("vat_category") or "S").upper()
        if cat == "S" and ln.get("vat_rate") is None:
            raise InputError(f"lines[{i-1}].vat_rate is required for VAT category S")
        rate = _dec(ln.get("vat_rate") or 0, f"lines[{i-1}].vat_rate")
        net = (q * price).quantize(D2, ROUND_HALF_UP)
        line_ac = ""
        if ln.get("allowances") or ln.get("charges"):  # B252: line allowances/charges (BG-27/BG-28), additive
            from .einvoice_cii import line_acs
            acs = line_acs(ln, f"lines[{i-1}]")
            line_ac = "".join(_ubl_ac(a, cur, False) for a in acs)
            net += sum((a["amount"] if a["charge"] else -a["amount"]) for a in acs)
        total += net
        key = (cat, rate.normalize() if cat != "O" else Decimal(0))
        groups[key] = groups.get(key, Decimal(0)) + net
        unit = ln.get("unit") or "C62"
        unit = RETIRED_UNITS.get(str(unit).strip().upper(), unit)
        qtag = "cbc:CreditedQuantity" if is_cn else "cbc:InvoicedQuantity"
        ltag = "cac:CreditNoteLine" if is_cn else "cac:InvoiceLine"
        item = f"<cac:Item>"
        if ln.get("item_description"):
            item += f"<cbc:Description>{_x(ln['item_description'])}</cbc:Description>"
        item += f"<cbc:Name>{_x(ln['description'][:200])}</cbc:Name>"
        if ln.get("seller_item_id"):
            item += f"<cac:SellersItemIdentification><cbc:ID>{_x(ln['seller_item_id'])}</cbc:ID></cac:SellersItemIdentification>"
        item += _taxcat(cat, rate) + "</cac:Item>"
        lines_xml.append(f"<{ltag}><cbc:ID>{i}</cbc:ID><{qtag} unitCode=\"{_x(unit)}\">{_num(q)}</{qtag}>"
                         f"<cbc:LineExtensionAmount currencyID=\"{cur}\">{_a(net)}</cbc:LineExtensionAmount>{line_ac}{item}"
                         f"<cac:Price><cbc:PriceAmount currencyID=\"{cur}\">{_num(price)}</cbc:PriceAmount></cac:Price></{ltag}>")
    doc_ac, allow_total, charge_total = [], Decimal(0), Decimal(0)
    if j.get("allowances") or j.get("charges"):  # B252: document allowances/charges (BG-20/BG-21), additive
        from .einvoice_cii import doc_acs
        doc_ac = doc_acs(j, groups)
        for a in doc_ac:
            gk = (a["cat"], a["rate"] if a["cat"] != "O" else Decimal(0))
            groups[gk] = groups.get(gk, Decimal(0)) + (a["amount"] if a["charge"] else -a["amount"])
            if a["charge"]:
                charge_total += a["amount"]
            else:
                allow_total += a["amount"]
    subt, vat_total = [], Decimal(0)
    for (cat, rate), base in groups.items():
        tax = Decimal(0) if cat in ("O", "E", "AE", "K", "G", "Z") else (base * rate / 100).quantize(D2, ROUND_HALF_UP)
        vat_total += tax
        s = f"<cac:TaxSubtotal><cbc:TaxableAmount currencyID=\"{cur}\">{_a(base)}</cbc:TaxableAmount><cbc:TaxAmount currencyID=\"{cur}\">{_a(tax)}</cbc:TaxAmount><cac:TaxCategory><cbc:ID>{cat}</cbc:ID>"
        if cat != "O":
            s += f"<cbc:Percent>{_num(rate)}</cbc:Percent>"
        if cat in EXEMPT_DEFAULTS:
            code, text = EXEMPT_DEFAULTS[cat]
            code = (j.get("vat_exemption_code") or {}).get(cat, code) if isinstance(j.get("vat_exemption_code"), dict) else code
            text = (j.get("vat_exemption_reason") or {}).get(cat, text) if isinstance(j.get("vat_exemption_reason"), dict) else text
            if code:
                s += f"<cbc:TaxExemptionReasonCode>{_x(code)}</cbc:TaxExemptionReasonCode>"
            s += f"<cbc:TaxExemptionReason>{_x(text)}</cbc:TaxExemptionReason>"
        s += "<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal>"
        subt.append(s)
    basis = total - allow_total + charge_total
    incl = basis + vat_total
    prepaid = _dec(j.get("prepaid_amount", 0), "prepaid_amount")
    payable = incl - prepaid
    cid = {"en16931": EN16931_ID, "xrechnung": XRECHNUNG_ID, "peppol-bis3": PEPPOL_ID}[fmt]
    root_tag, root_ns = ("CreditNote", NS["ubl_cn"]) if is_cn else ("Invoice", NS["ubl_inv"])
    x = [f'<?xml version="1.0" encoding="UTF-8"?>\n<{root_tag} xmlns="{root_ns}" xmlns:cac="{NS["cac"]}" xmlns:cbc="{NS["cbc"]}">',
         f"<cbc:CustomizationID>{cid}</cbc:CustomizationID>"]
    if fmt in ("peppol-bis3", "xrechnung"):
        x.append("<cbc:ProfileID>urn:fdc:peppol.eu:2017:poacc:billing:01:1.0</cbc:ProfileID>")
    x.append(f"<cbc:ID>{_x(j['invoice_number'])}</cbc:ID><cbc:IssueDate>{_x(j['issue_date'])}</cbc:IssueDate>")
    if j.get("due_date") and not is_cn:
        x.append(f"<cbc:DueDate>{_x(j['due_date'])}</cbc:DueDate>")
    x.append(f"<cbc:{'CreditNoteTypeCode' if is_cn else 'InvoiceTypeCode'}>{_x(j.get('type_code', '381' if is_cn else '380'))}</cbc:{'CreditNoteTypeCode' if is_cn else 'InvoiceTypeCode'}>")
    if j.get("note"):
        x.append(f"<cbc:Note>{_x(j['note'])}</cbc:Note>")
    if j.get("tax_point_date"):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(j["tax_point_date"])):
            raise InputError("tax_point_date must be YYYY-MM-DD")
        x.append(f"<cbc:TaxPointDate>{_x(j['tax_point_date'])}</cbc:TaxPointDate>")  # BT-7
    x.append(f"<cbc:DocumentCurrencyCode>{cur}</cbc:DocumentCurrencyCode>")
    bref = j.get("buyer_reference") or (None if j.get("order_reference") or fmt == "en16931" else j["invoice_number"] if fmt == "peppol-bis3" else None)
    if bref:
        x.append(f"<cbc:BuyerReference>{_x(bref)}</cbc:BuyerReference>")
    if j.get("order_reference"):
        x.append(f"<cac:OrderReference><cbc:ID>{_x(j['order_reference'])}</cbc:ID></cac:OrderReference>")
    if j.get("preceding_invoice"):
        x.append(f"<cac:BillingReference><cac:InvoiceDocumentReference><cbc:ID>{_x(j['preceding_invoice'])}</cbc:ID></cac:InvoiceDocumentReference></cac:BillingReference>")
    x.append(f"<cac:AccountingSupplierParty>{_party(j['seller'], 'seller', fmt, cur)}</cac:AccountingSupplierParty>")
    x.append(f"<cac:AccountingCustomerParty>{_party(j['buyer'], 'buyer', fmt, cur)}</cac:AccountingCustomerParty>")
    if j.get("delivery_date") or j.get("delivery_country"):
        dl = f"<cbc:ActualDeliveryDate>{_x(j['delivery_date'])}</cbc:ActualDeliveryDate>" if j.get("delivery_date") else ""
        if j.get("delivery_country"):  # B252: BT-80 (intra-Community supplies, BR-IC-12)
            dl += (f"<cac:DeliveryLocation><cac:Address><cac:Country><cbc:IdentificationCode>{_x(str(j['delivery_country']).upper())}"
                   "</cbc:IdentificationCode></cac:Country></cac:Address></cac:DeliveryLocation>")
        x.append(f"<cac:Delivery>{dl}</cac:Delivery>")
    pay = j.get("payment") or {}
    if pay.get("iban") or pay.get("means_code"):
        pm = f"<cac:PaymentMeans><cbc:PaymentMeansCode>{_x(pay.get('means_code', '58' if pay.get('iban') else '1'))}</cbc:PaymentMeansCode>"
        if pay.get("reference"):
            pm += f"<cbc:PaymentID>{_x(pay['reference'])}</cbc:PaymentID>"
        if pay.get("iban"):
            pm += f"<cac:PayeeFinancialAccount><cbc:ID>{_x(pay['iban'].replace(' ', ''))}</cbc:ID>"
            if pay.get("account_name"):
                pm += f"<cbc:Name>{_x(pay['account_name'])}</cbc:Name>"
            if pay.get("bic"):
                pm += f"<cac:FinancialInstitutionBranch><cbc:ID>{_x(pay['bic'])}</cbc:ID></cac:FinancialInstitutionBranch>"
            pm += "</cac:PayeeFinancialAccount>"
        x.append(pm + "</cac:PaymentMeans>")
    if pay.get("terms"):
        x.append(f"<cac:PaymentTerms><cbc:Note>{_x(pay['terms'])}</cbc:Note></cac:PaymentTerms>")
    x += [_ubl_ac(a, cur, True) for a in doc_ac]
    x.append(f"<cac:TaxTotal><cbc:TaxAmount currencyID=\"{cur}\">{_a(vat_total)}</cbc:TaxAmount>{''.join(subt)}</cac:TaxTotal>")
    x.append(f"<cac:LegalMonetaryTotal><cbc:LineExtensionAmount currencyID=\"{cur}\">{_a(total)}</cbc:LineExtensionAmount>"
             f"<cbc:TaxExclusiveAmount currencyID=\"{cur}\">{_a(basis)}</cbc:TaxExclusiveAmount>"
             f"<cbc:TaxInclusiveAmount currencyID=\"{cur}\">{_a(incl)}</cbc:TaxInclusiveAmount>"
             + (f"<cbc:AllowanceTotalAmount currencyID=\"{cur}\">{_a(allow_total)}</cbc:AllowanceTotalAmount>"
                f"<cbc:ChargeTotalAmount currencyID=\"{cur}\">{_a(charge_total)}</cbc:ChargeTotalAmount>" if doc_ac else "")
             + (f"<cbc:PrepaidAmount currencyID=\"{cur}\">{_a(prepaid)}</cbc:PrepaidAmount>" if prepaid else "")
             + f"<cbc:PayableAmount currencyID=\"{cur}\">{_a(payable)}</cbc:PayableAmount></cac:LegalMonetaryTotal>")
    x += lines_xml
    x.append(f"</{root_tag}>")
    totals = {"currency": cur, "line_total": _a(total), "vat_total": _a(vat_total), "total_with_vat": _a(incl), "payable": _a(payable)}
    if doc_ac:
        totals.update({"allowance_total": _a(allow_total), "charge_total": _a(charge_total), "tax_basis_total": _a(basis)})
    return "".join(x), totals


def create_cii(j: dict, fmt: str) -> dict:
    """B252: format cii | xrechnung-cii (CII XML) or zugferd | factur-x | zugferd-xrechnung (PDF/A-3 hybrid + CII)."""
    import base64
    from . import einvoice_cii as C
    xml, totals, m = C.build_cii(j, fmt)
    gid, is_pdf, fname, level = C.CII_FORMATS[fmt]
    xr = gid == XRECHNUNG_ID
    v = validate(xml.encode("utf-8"), "xrechnung" if xr else "en16931")
    notes = []
    if any(str(ln.get("unit") or "").strip().upper() in RETIRED_UNITS for ln in (j.get("lines") or []) if isinstance(ln, dict)):
        notes.append("Unit code PK (pack) was retired from UN/ECE Recommendation 20 and is rejected by BR-CL-23; written as XPK (package).")
    r = {"valid": v["valid"], "format": fmt, "syntax": "UN/CEFACT CII D16B", "document_type": v["document_type"],
         "type_code": str(j.get("type_code") or ("381" if m["is_cn"] else "380")), "guideline_id": gid, "profile": v["profile"],
         "totals": totals, "xml": xml, "errors": v["errors"], "warnings": v["warnings"], "checked": v["checked"],
         "notes": notes, "disclaimer": DISCLAIMER}
    if is_pdf:
        r["embedded_file"] = fname
        r["zugferd_profile"] = "EN 16931 (COMFORT)" if level == "EN 16931" else "XRECHNUNG"
        if not v["valid"]:
            r["pdf_base64"] = None
            notes.append("No PDF was built because the embedded XML has errors: fix them and call again.")
        else:
            from . import einvoice_pdf
            pdf = einvoice_pdf.build(j, m, xml, fmt, fname, level)
            back, meta = extract_xml(pdf)  # round trip: the XML a receiver reads back is exactly the validated one
            if back != xml.encode("utf-8") or meta.get("embedded_file") != fname:
                raise RuntimeError("ZUGFeRD PDF round trip failed")
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(j["invoice_number"]))[:80] or "invoice"
            r.update({"pdf_base64": base64.b64encode(pdf).decode("ascii"), "pdf_filename": f"{safe}.pdf",
                      "pdf": {"standard": "PDF/A-3b", "hybrid": ("ZUGFeRD 2.3 / Factur-X 1.07 EN 16931" if level == "EN 16931" else "ZUGFeRD 2.3 XRECHNUNG"),
                              "af_relationship": "Alternative", "bytes": len(pdf), "sha256": hashlib.sha256(pdf).hexdigest()}})
            notes.append("Send the PDF (pdf_base64, or call with Accept: application/pdf to get the raw file). The embedded "
                         f"{fname} is the invoice data the receiver's software reads; the visual layout is for people.")
    if xr:
        notes.append("XRechnung 3.0 in CII syntax: checked against EN 16931 and the KoSIT XRechnung 3.0.2 rules.")
    return r


def create(j: dict) -> dict:
    from .einvoice_cii import CII_FORMATS, norm_format
    if isinstance(j, dict) and norm_format(j.get("format")) in CII_FORMATS:
        return create_cii(j, norm_format(j.get("format")))
    xml, totals = build_ubl(j)
    fmt = (j.get("format") or "en16931").lower()
    v = validate(xml.encode("utf-8"), "xrechnung" if fmt == "xrechnung" else "en16931")
    notes = []
    if any(str(ln.get("unit") or "").strip().upper() in RETIRED_UNITS for ln in (j.get("lines") or []) if isinstance(ln, dict)):
        notes.append("Unit code PK (pack) was retired from UN/ECE Recommendation 20 and is rejected by BR-CL-23; written as XPK (package).")
    if fmt == "peppol-bis3":
        notes.append("Built to the Peppol BIS Billing 3.0 structure and checked against EN 16931 only; Peppol-specific rules are not run yet.")
    r = {"valid": v["valid"], "format": fmt, "syntax": "UBL 2.1", "document_type": v["document_type"],
         "totals": totals, "xml": xml, "errors": v["errors"], "warnings": v["warnings"], "checked": v["checked"],
         "notes": notes, "disclaimer": DISCLAIMER}
    if v.get("slovakia"):
        r["slovakia"] = v["slovakia"]
    if v.get("norway"):
        r["norway"] = v["norway"]
    return r
