"""Norwegian national field checks for e-invoices (Bookkeeping Act e-invoicing duty from 2027-01-01).

These are NOT the official Peppol BIS 3 / EHF schematron rules (NO-R-*), which W System does not run.
They are W System's own checks, written from Norwegian law and the public register rules:
  - Bokføringsforskriften (Bookkeeping Regulation) ch. 5 on sales documents: seller org. no.,
    "MVA" after the org. no. for VAT-registered sellers; buyer org. no. on sales documents from 2028
    (regulation 2026-09-29-1933)  https://lovdata.no/forskrift/2004-12-01-1558
  - Foretaksregisterloven § 10-2: AS, ASA and NUF state "Foretaksregisteret" on business documents
    https://lovdata.no/lov/1985-06-21-78
  - Brønnøysund organisation number: 9 digits, modulus-11 check digit (weights 3 2 7 6 5 4 3 2)
  - Norwegian VAT rates 25 %, 15 %, 12 % (Skatteetaten)
Each finding is a WARNING (severity "warning", ruleset "NO-LAW"), never an error, so the EN 16931 verdict is unchanged.
Runs on UBL documents whose seller country (BT-40) is NO.
"""
from __future__ import annotations
import re

VERSION = "W System NO field checks 2026-10-21"
SOURCE = "https://wsystem.dev/norway-e-invoicing-2027-software"
NO_RATES = {"25", "15", "12"}
WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


def orgnr_ok(n: str) -> bool:
    """Brønnøysund organisation number check digit (mod 11)."""
    if not re.fullmatch(r"\d{9}", n or ""):
        return False
    r = 11 - sum(int(a) * b for a, b in zip(n[:8], WEIGHTS)) % 11
    r = 0 if r == 11 else r
    return r != 10 and r == int(n[8])


def _t(node, path, ns):
    v = node.findtext(path, namespaces=ns) if node is not None else None
    return (v or "").strip()


def _w(rid, msg, bts, fix):
    return {"rule_id": rid, "severity": "warning", "ruleset": "NO-LAW", "message": msg,
            "location": None, "business_terms": bts, "fix": fix}


def _num(s: str) -> str:
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else str(f)
    except ValueError:
        return s


def _tax_ids(party, ns):
    """(vat_company_id, other_tax_scheme_company_ids)"""
    vat, other = "", []
    for pts in (party.findall("cac:PartyTaxScheme", ns) if party is not None else []):
        cid = _t(pts, "cbc:CompanyID", ns)
        if _t(pts, "cac:TaxScheme/cbc:ID", ns).upper() == "VAT":
            vat = cid.replace(" ", "").upper()
        else:
            other.append(cid)
    return vat, other


def check(root, syntax: str, ns: dict) -> tuple[list[dict], dict]:
    """Return (warnings, no_block). no_block['applies'] is False when the seller is not Norwegian or syntax is CII."""
    if syntax != "ubl":
        cc = ""
        try:
            cc = (root.findtext(".//ram:SellerTradeParty/ram:PostalTradeAddress/ram:CountryID", namespaces=ns) or "").strip().upper()
        except Exception:
            pass
        if cc == "NO":
            return [], {"applies": False, "note": "Norwegian field checks run on UBL only (Norwegian e-invoices over Peppol use EHF / Peppol BIS Billing 3.0 in UBL 2.1); this CII file was checked against EN 16931 only."}
        return [], {"applies": False}
    sup = root.find("cac:AccountingSupplierParty/cac:Party", ns)
    cus = root.find("cac:AccountingCustomerParty/cac:Party", ns)
    if _t(sup, "cac:PostalAddress/cac:Country/cbc:IdentificationCode", ns).upper() != "NO":
        return [], {"applies": False}
    buyer_cc = _t(cus, "cac:PostalAddress/cac:Country/cbc:IdentificationCode", ns).upper()
    out: list[dict] = []
    if "peppol.eu:2017:poacc:billing:3.0" not in _t(root, "cbc:CustomizationID", ns):
        out.append(_w("NO-LAW-01", "Norwegian e-invoices are exchanged over Peppol as EHF / Peppol BIS Billing 3.0 (UBL 2.1); this file's CustomizationID (BT-24) is not the Peppol BIS 3 identifier.",
                      ["BT-24"], "Use CustomizationID urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0 (create with format=peppol-bis3)."))
    # seller org. no.
    s_vat, s_other = _tax_ids(sup, ns)
    s_legal = _t(sup, "cac:PartyLegalEntity/cbc:CompanyID", ns).replace(" ", "")
    s_org = s_legal or (s_vat[2:11] if re.fullmatch(r"NO\d{9}(MVA)?", s_vat) else "")
    if not s_legal:
        out.append(_w("NO-LAW-02", "Seller legal registration identifier (BT-30) is empty. A Norwegian sales document states the seller's organisation number.",
                      ["BT-30", "BT-30-1"], "Add the 9-digit organisation number in cac:PartyLegalEntity/cbc:CompanyID schemeID=\"0192\" (create: seller.company_id)."))
    if s_org and not orgnr_ok(s_org):
        out.append(_w("NO-LAW-03", f"Seller organisation number {s_org} fails the Brønnøysund modulus-11 check digit, so it can't be a real organisation number.",
                      ["BT-30", "BT-31"], "Check the number at https://data.brreg.no/enhetsregisteret/oppslag/enheter or GET /company."))
    if s_vat and s_vat.startswith("NO") and not re.fullmatch(r"NO\d{9}MVA", s_vat):
        out.append(_w("NO-LAW-04", f"Seller VAT identifier {s_vat}: a seller registered in the Norwegian VAT register shows the organisation number followed by 'MVA' (format NO + 9 digits + MVA).",
                      ["BT-31"], "Use e.g. NO923609016MVA in the VAT PartyTaxScheme."))
    # Foretaksregisteret (§ 10-2) - only detectable from the legal form text
    legal_form = _t(sup, "cac:PartyLegalEntity/cbc:CompanyLegalForm", ns)
    name = _t(sup, "cac:PartyLegalEntity/cbc:RegistrationName", ns) or _t(sup, "cac:PartyName/cbc:Name", ns)
    looks_as = bool(re.search(r"\b(ASA|AS|NUF)\s*$", name)) or bool(re.search(r"\b(ASA|AS|NUF)\b", legal_form))
    has_fr = any("foretaksregisteret" in x.lower() for x in s_other + [legal_form])
    if looks_as and not has_fr:
        out.append(_w("NO-LAW-05", f"Seller '{name}' looks like an AS, ASA or NUF, but the word 'Foretaksregisteret' is not on the invoice (foretaksregisterloven § 10-2).",
                      ["BT-32", "BT-33"], "Add a PartyTaxScheme with CompanyID 'Foretaksregisteret' and TaxScheme ID 'TAX' (create does this when seller.legal_form is AS/ASA/NUF)."))
    # seller endpoint
    ep = sup.find("cbc:EndpointID", ns) if sup is not None else None
    if ep is not None and (ep.get("schemeID") or "").strip() == "0192":
        eid = (ep.text or "").strip()
        if not orgnr_ok(eid):
            out.append(_w("NO-LAW-06", f"Seller electronic address 0192:{eid} is not a valid 9-digit organisation number; Norwegian Peppol participants are 0192:<org. no.>.",
                          ["BT-34", "BT-34-1"], "Use the organisation number only (no NO prefix, no MVA suffix)."))
    # buyer
    if buyer_cc == "NO":
        b_legal = _t(cus, "cac:PartyLegalEntity/cbc:CompanyID", ns).replace(" ", "")
        if not b_legal:
            out.append(_w("NO-LAW-07", "Buyer organisation number (BT-47) is empty. From 2028 Norwegian sales documents to business buyers must state the buyer's organisation number, and it is how the buyer is found in Peppol (0192).",
                          ["BT-47", "BT-47-1"], "Add the buyer's organisation number in cac:PartyLegalEntity/cbc:CompanyID schemeID=\"0192\" (create: buyer.company_id)."))
        elif not orgnr_ok(b_legal):
            out.append(_w("NO-LAW-08", f"Buyer organisation number {b_legal} fails the modulus-11 check digit.", ["BT-47"], "Check the buyer's number in the Brønnøysund register."))
        bep = cus.find("cbc:EndpointID", ns) if cus is not None else None
        if bep is not None and (bep.get("schemeID") or "").strip() == "0192" and not orgnr_ok((bep.text or "").strip()):
            out.append(_w("NO-LAW-09", f"Buyer electronic address 0192:{(bep.text or '').strip()} is not a valid organisation number.",
                          ["BT-49", "BT-49-1"], "Look the buyer up with GET /einvoice/route before sending."))
        for st in root.findall("cac:TaxTotal/cac:TaxSubtotal", ns):
            cat = _t(st, "cac:TaxCategory/cbc:ID", ns).upper()
            rate = _num(_t(st, "cac:TaxCategory/cbc:Percent", ns) or "0")
            if cat == "S" and rate not in NO_RATES:
                out.append(_w("NO-LAW-10", f"Standard-rated (S) VAT breakdown uses {rate} %; Norwegian rates are 25 %, 15 % (food) and 12 %.",
                              ["BT-119", "BT-118"], "Check the VAT rate for this domestic supply."))
    block = {"applies": True, "version": VERSION, "source": SOURCE,
             "checks": "W System field checks from Norwegian law and the Brønnøysund number rules; not the official EHF / Peppol BIS (NO-R) schematron.",
             "seller_org_number": s_org or None, "seller_org_number_valid": orgnr_ok(s_org) if s_org else None,
             "participant_id": f"0192:{s_org}" if s_org and orgnr_ok(s_org) else None}
    return out, block
