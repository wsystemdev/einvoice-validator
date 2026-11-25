"""Slovak national field checks for e-invoices (eFaktúra, mandatory from 2027-01-01).

These are NOT the official Peppol BIS 3 / Slovak schematron rules. They are W System's own checks,
written from public Financial Administration (FR SR) documents:
  - "Transpozície štandardu Peppol BIS v podmienkach Slovenskej legislatívy" v1.13 (29 Oct 2026; v1.13 only dropped the recommended 4-decimal rounding at BT-129/146/148, no check affected)
    https://www.financnasprava.sk/sk/podnikatelia/dane/dan-z-pridanej-hodnoty/e-faktura#InfoSW
  - FR SR FAQ 9/DPH/2025/IM (12 Oct 2026 edition), part III (VAT category / VATEX mapping)
  - Peppol Authority SK specific requirements (0245:DIČ participant ids)
Each finding is a WARNING (severity "warning", ruleset "SK-FS"), never an error, so the EN 16931 verdict is unchanged.
Runs on UBL documents whose seller country (BT-40) is SK.
"""
from __future__ import annotations
import re, uuid

TRANSPOSITION = "FR SR Peppol BIS transposition v1.13 (2026-10-29)"
SOURCE = "https://www.financnasprava.sk/sk/podnikatelia/dane/dan-z-pridanej-hodnoty/e-faktura"
UUID_NAMESPACE = uuid.UUID("3fd00464-9f15-52ce-8cdc-6dd374d5ad42")
SK_RATES = {"23", "19", "5"}
EXEMPT_CATS = {"E", "AE", "K", "G", "O"}


def _t(node, path, ns):
    v = node.findtext(path, namespaces=ns) if node is not None else None
    return (v or "").strip()


def _w(rid, msg, bts, fix, location=None):
    return {"rule_id": rid, "severity": "warning", "ruleset": "SK-FS", "message": msg,
            "location": location, "business_terms": bts, "fix": fix}


def _num(s: str) -> str:
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else str(f)
    except ValueError:
        return s


def invoice_uuid(scheme: str, endpoint: str, type_code: str, number: str, issue_date: str) -> str:
    """UUIDv5 of 'BT-34@schemeID BT-34 BT-3 BT-1 BT-2' (single spaces, UTF-8) in the fixed FR SR namespace."""
    name = " ".join(x.strip() for x in (scheme, endpoint, type_code, number, issue_date))
    return str(uuid.uuid5(UUID_NAMESPACE, name))


def check(root, syntax: str, ns: dict) -> tuple[list[dict], dict]:
    """Return (warnings, sk_block). sk_block['applies'] is False when the seller is not Slovak or syntax is CII."""
    if syntax != "ubl":
        seller_cc = ""
        try:
            seller_cc = (root.findtext(".//ram:SellerTradeParty/ram:PostalTradeAddress/ram:CountryID", namespaces=ns) or "").strip().upper()
        except Exception:
            pass
        if seller_cc == "SK":
            return [], {"applies": False, "note": "Slovak national field checks run on UBL only (Slovak eFaktúra over Peppol uses UBL 2.1 / Peppol BIS Billing 3.0); this CII file was checked against EN 16931 only."}
        return [], {"applies": False}
    sup = root.find("cac:AccountingSupplierParty/cac:Party", ns)
    cus = root.find("cac:AccountingCustomerParty/cac:Party", ns)
    seller_cc = _t(sup, "cac:PostalAddress/cac:Country/cbc:IdentificationCode", ns).upper()
    if seller_cc != "SK":
        return [], {"applies": False}
    buyer_cc = _t(cus, "cac:PostalAddress/cac:Country/cbc:IdentificationCode", ns).upper()
    is_cn = root.tag.endswith("}CreditNote")
    out: list[dict] = []
    cid = _t(root, "cbc:CustomizationID", ns)
    if "peppol.eu:2017:poacc:billing:3.0" not in cid:
        out.append(_w("SK-FS-01", "Slovak eFaktúra is exchanged over Peppol as Peppol BIS Billing 3.0 (UBL 2.1); this file's CustomizationID (BT-24) is not the Peppol BIS 3 identifier.",
                      ["BT-24"], "Use CustomizationID urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0 and ProfileID urn:fdc:peppol.eu:2017:poacc:billing:01:1.0 (create with format=peppol-bis3)."))
    # seller VAT / DIČ
    vat = ""
    for pts in (sup.findall("cac:PartyTaxScheme", ns) if sup is not None else []):
        if _t(pts, "cac:TaxScheme/cbc:ID", ns).upper() == "VAT":
            vat = _t(pts, "cbc:CompanyID", ns).replace(" ", "").upper()
    dic = vat[2:] if re.fullmatch(r"SK\d{10}", vat) else ""
    # BT-34 seller endpoint
    ep = sup.find("cbc:EndpointID", ns) if sup is not None else None
    ep_id, ep_scheme = ((ep.text or "").strip(), (ep.get("schemeID") or "").strip()) if ep is not None else ("", "")
    if not ep_id:
        out.append(_w("SK-FS-02", "Seller electronic address (BT-34) is mandatory in the Slovak transposition (1..1); it also feeds the invoice UUID.",
                      ["BT-34", "BT-34-1"], "Add cbc:EndpointID schemeID=\"0245\" with the seller's 10-digit DIČ (the digits after 'SK' in the IČ DPH)."))
    elif ep_scheme != "0245" or not re.fullmatch(r"\d{10}", ep_id):
        out.append(_w("SK-FS-03", f"Seller electronic address is {ep_scheme or '?'}:{ep_id}; Slovak participants are registered as 0245:<10-digit DIČ>.",
                      ["BT-34", "BT-34-1"], "Use schemeID 0245 and the 10-digit DIČ (not 9950 + IČ DPH)."))
    elif dic and ep_id != dic:
        out.append(_w("SK-FS-04", f"Seller endpoint 0245:{ep_id} does not match the DIČ in the seller VAT number {vat}.",
                      ["BT-34", "BT-31"], "The Slovak IČ DPH is 'SK' + DIČ; the EndpointID should normally be the same 10 digits."))
    # BT-49 buyer endpoint
    bep = cus.find("cbc:EndpointID", ns) if cus is not None else None
    b_id, b_scheme = ((bep.text or "").strip(), (bep.get("schemeID") or "").strip()) if bep is not None else ("", "")
    if not b_id:
        out.append(_w("SK-FS-05", "Buyer electronic address (BT-49) is mandatory in the Slovak transposition (1..1).",
                      ["BT-49", "BT-49-1"], "Add the buyer's cbc:EndpointID; for a Slovak buyer schemeID=\"0245\" and the 10-digit DIČ. Check it on Peppol (GET /einvoice/route)."))
    elif buyer_cc == "SK" and (b_scheme != "0245" or not re.fullmatch(r"\d{10}", b_id)):
        out.append(_w("SK-FS-06", f"Buyer electronic address is {b_scheme or '?'}:{b_id}; Slovak buyers are addressed as 0245:<10-digit DIČ>.",
                      ["BT-49", "BT-49-1"], "Use schemeID 0245 and the buyer's 10-digit DIČ."))
    # addresses (EN 16931 0..1, required by VAT Act s. 74(1)(a)/(b))
    for party, role, bts in ((sup, "Seller", ("BT-35", "BT-37", "BT-38")), (cus, "Buyer", ("BT-50", "BT-52", "BT-53"))):
        missing = [bt for bt, path in zip(bts, ("cac:PostalAddress/cbc:StreetName", "cac:PostalAddress/cbc:CityName", "cac:PostalAddress/cbc:PostalZone"))
                   if not _t(party, path, ns)]
        if missing:
            out.append(_w("SK-FS-07" if role == "Seller" else "SK-FS-08",
                          f"{role} address is incomplete ({', '.join(missing)} missing). EN 16931 makes these optional, but the Slovak VAT Act (s. 74(1)({'a' if role == 'Seller' else 'b'}) requires the address on the invoice.",
                          missing, f"Fill {role.lower()} street, city and postal code."))
    # BT-30 / BT-33 / BT-47 (Commercial Code s. 3a)
    if not _t(sup, "cac:PartyLegalEntity/cbc:CompanyID", ns):
        out.append(_w("SK-FS-09", "Seller legal registration identifier (BT-30, the IČO) is conditionally required: give it if an IČO has been assigned.",
                      ["BT-30", "BT-30-1"], "Add the seller IČO in cac:PartyLegalEntity/cbc:CompanyID (with schemeID from the ICD list)."))
    if not _t(sup, "cac:PartyLegalEntity/cbc:CompanyLegalForm", ns):
        out.append(_w("SK-FS-10", "Seller additional legal information (BT-33) is conditionally required: legal form and the register entry (court, section, insert number) for registered businesses (Commercial Code s. 3a).",
                      ["BT-33"], "Example: 'Spoločnosť s ručením obmedzeným, zapísaná v Obchodnom registri Okresného súdu Žilina, oddiel Sro, vložka č. 12345/L' in cbc:CompanyLegalForm."))
    if buyer_cc == "SK" and not _t(cus, "cac:PartyLegalEntity/cbc:CompanyID", ns):
        out.append(_w("SK-FS-11", "Buyer legal registration identifier (BT-47, the IČO) is conditionally required if an IČO has been assigned.",
                      ["BT-47", "BT-47-1"], "Add the buyer IČO in cac:PartyLegalEntity/cbc:CompanyID if it has one."))
    # BT-7 / BT-8
    issue = _t(root, "cbc:IssueDate", ns)
    tax_point = _t(root, "cbc:TaxPointDate", ns)
    if root.find("cac:InvoicePeriod/cbc:DescriptionCode", ns) is not None:
        out.append(_w("SK-FS-12", "The Slovak transposition does not use BT-8 (VAT point date code); the tax point date goes in BT-7.",
                      ["BT-8", "BT-7"], "Remove cac:InvoicePeriod/cbc:DescriptionCode and use cbc:TaxPointDate."))
    delivery = _t(root, "cac:Delivery/cbc:ActualDeliveryDate", ns)
    if not tax_point and delivery and delivery != issue:
        out.append(_w("SK-FS-13", f"Delivery date {delivery} differs from the issue date {issue} but BT-7 (tax point date, DUZP) is empty. In Slovakia BT-7 is required when it is known and differs from the issue date (VAT Act s. 74(1)(d)).",
                      ["BT-7", "BT-72"], "Add cbc:TaxPointDate (create: tax_point_date)."))
    # VAT breakdown
    for st in root.findall("cac:TaxTotal/cac:TaxSubtotal", ns):
        cat = _t(st, "cac:TaxCategory/cbc:ID", ns).upper()
        rate = _num(_t(st, "cac:TaxCategory/cbc:Percent", ns) or "0")
        if cat == "S" and rate not in SK_RATES and buyer_cc == "SK":
            out.append(_w("SK-FS-14", f"Standard-rated (S) VAT breakdown uses {rate} %; Slovak rates are 23 %, 19 % and 5 %.",
                          ["BT-119", "BT-118"], "Check the VAT rate for this domestic supply."))
        if cat in EXEMPT_CATS:
            if not _t(st, "cac:TaxCategory/cbc:TaxExemptionReasonCode", ns):
                out.append(_w("SK-FS-15", f"VAT category {cat} has no VATEX exemption reason code (BT-121). FR SR recommends using BT-121 primarily, with BT-120 text as the explanation (FAQ part III mapping).",
                              ["BT-121", "BT-120"], "E.g. VATEX-EU-IC for s. 43 intra-EU supply (K), VATEX-EU-G for exports (G), VATEX-EU-AE for reverse charge (AE), VATEX-EU-O for not subject (O), VATEX-EU-132-1x / VATEX-EU-135-1 for s. 28-41 exemptions (E)."))
            if not _t(st, "cac:TaxCategory/cbc:TaxExemptionReason", ns):
                out.append(_w("SK-FS-16", f"VAT category {cat} has no exemption reason text (BT-120). It is conditionally required where the VAT Act asks for a specific wording (s. 74(1)(h), (k)-(n), e.g. 'prenesenie daňovej povinnosti' for reverse charge).",
                              ["BT-120"], "Add cbc:TaxExemptionReason with the legal wording / reference."))
    type_code = _t(root, "cbc:CreditNoteTypeCode" if is_cn else "cbc:InvoiceTypeCode", ns)
    if type_code == "384":
        out.append(_w("SK-FS-17", "Type code 384 (corrected invoice): FR SR currently recommends a credit note plus a new invoice for corrections; 384 is planned for a later Peppol BIS release.",
                      ["BT-3"], "Issue a credit note (381) and a new invoice (380) instead."))
    block = {"applies": True, "transposition": TRANSPOSITION, "source": SOURCE, "checks": "W System field checks from the FR SR transposition; not the official Slovak/Peppol schematron.",
             "seller_dic": dic or None, "participant_id": f"0245:{dic}" if dic else None}
    number = _t(root, "cbc:ID", ns)
    if ep_id and ep_scheme and type_code and number and issue:
        block["invoice_uuid"] = invoice_uuid(ep_scheme, ep_id, type_code, number, issue)
        block["invoice_uuid_input"] = " ".join((ep_scheme, ep_id, type_code, number, issue))
        block["invoice_uuid_note"] = ("UUIDv5 over 'BT-34 schemeID, BT-34, BT-3, BT-1, BT-2' joined by single spaces, namespace 3fd00464-9f15-52ce-8cdc-6dd374d5ad42, per the BT-34 note in the "
                                      "transposition. The access points (C2/C3) compute the official value; the published example in the document is inconsistent, so treat this as a cross-check.")
    return out, block
