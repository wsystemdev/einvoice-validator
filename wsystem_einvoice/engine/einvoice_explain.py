"""Plain-English explanations for e-invoice rule failures (safe send, v0.3.77).

explain(err) adds {"what", "field", "json_field", "fix"} to a validation error: what is wrong, which invoice field it is
(business term name + BT number, and the matching POST /einvoice/create JSON field where there is one) and how to fix it.
Common EN 16931 / XRechnung rules have hand-written text; every other rule gets a generic explanation built from the
official message and the business terms it names, so no error comes back without one. The official rule ID and message
stay untouched next to it."""
from __future__ import annotations
import re

# BT number -> (plain name, JSON field of POST /einvoice/create or None)
BT = {
    "BT-1": ("Invoice number", "invoice_number"), "BT-2": ("Issue date", "issue_date"), "BT-3": ("Invoice type code", "type_code"),
    "BT-5": ("Invoice currency", "currency"), "BT-7": ("Tax point date", "tax_point_date"), "BT-8": ("Tax point date code", None),
    "BT-9": ("Payment due date", "due_date"), "BT-10": ("Buyer reference (Leitweg-ID in Germany)", "buyer_reference"),
    "BT-13": ("Purchase order reference", "order_reference"), "BT-20": ("Payment terms", "payment.terms"),
    "BT-22": ("Invoice note", "note"), "BT-24": ("Specification identifier (CustomizationID)", "format"),
    "BT-25": ("Preceding invoice reference", "preceding_invoice"), "BT-27": ("Seller name", "seller.name"),
    "BT-29": ("Seller identifier", "seller.company_id"), "BT-30": ("Seller legal registration ID", "seller.company_id"),
    "BT-31": ("Seller VAT ID", "seller.vat_id"), "BT-32": ("Seller tax number", "seller.tax_number"),
    "BT-33": ("Seller legal form", "seller.legal_form"), "BT-34": ("Seller electronic address (Peppol ID)", "seller.endpoint_id"),
    "BT-35": ("Seller street", "seller.street"), "BT-37": ("Seller city", "seller.city"), "BT-38": ("Seller postal code", "seller.postal_code"),
    "BT-40": ("Seller country code", "seller.country"), "BT-41": ("Seller contact name", "seller.contact_name"),
    "BT-42": ("Seller contact phone", "seller.phone"), "BT-43": ("Seller contact e-mail", "seller.email"),
    "BT-44": ("Buyer name", "buyer.name"), "BT-46": ("Buyer identifier", "buyer.company_id"),
    "BT-47": ("Buyer legal registration ID", "buyer.company_id"), "BT-48": ("Buyer VAT ID", "buyer.vat_id"),
    "BT-49": ("Buyer electronic address (Peppol ID)", "buyer.endpoint_id"), "BT-50": ("Buyer street", "buyer.street"),
    "BT-52": ("Buyer city", "buyer.city"), "BT-53": ("Buyer postal code", "buyer.postal_code"), "BT-55": ("Buyer country code", "buyer.country"), "BT-56": ("Buyer contact name", "buyer.contact_name"),
    "BT-63": ("Seller tax representative VAT ID", None), "BT-72": ("Delivery date", "delivery_date"),
    "BT-77": ("Deliver-to city", None), "BT-78": ("Deliver-to postal code", None), "BT-80": ("Deliver-to country code", None),
    "BT-81": ("Payment means code", "payment.means_code"), "BT-83": ("Payment reference", "payment.reference"),
    "BT-84": ("Payee IBAN / account", "payment.iban"), "BT-106": ("Sum of invoice line net amounts", None),
    "BT-107": ("Sum of allowances", None), "BT-108": ("Sum of charges", None), "BT-109": ("Invoice total without VAT", None),
    "BT-110": ("Invoice total VAT", None), "BT-112": ("Invoice total with VAT", None), "BT-113": ("Paid amount", "prepaid_amount"),
    "BT-114": ("Rounding amount", None), "BT-115": ("Amount due for payment", None),
    "BT-116": ("VAT category taxable amount", None), "BT-117": ("VAT category tax amount", None),
    "BT-118": ("VAT category code", "lines[].vat_category"), "BT-119": ("VAT category rate", "lines[].vat_rate"),
    "BT-120": ("VAT exemption reason text", "vat_exemption_reason"), "BT-121": ("VAT exemption reason code", "vat_exemption_code"),
    "BT-126": ("Invoice line ID", None), "BT-129": ("Invoiced quantity", "lines[].quantity"), "BT-130": ("Unit of measure", "lines[].unit"),
    "BT-131": ("Invoice line net amount", None), "BT-146": ("Item net price", "lines[].unit_price"),
    "BT-151": ("Line VAT category code", "lines[].vat_category"), "BT-152": ("Line VAT rate", "lines[].vat_rate"),
    "BT-153": ("Item name", "lines[].description"),
    "BG-5": ("Seller postal address", "seller.street/city/postal_code/country"), "BG-6": ("Seller contact", "seller.contact_name/phone/email"),
    "BG-8": ("Buyer postal address", "buyer.street/city/postal_code/country"), "BG-16": ("Payment instructions", "payment"),
    "BG-17": ("Credit transfer (IBAN)", "payment.iban"), "BG-23": ("VAT breakdown", "lines[].vat_rate"), "BG-25": ("Invoice line", "lines"),
}

_TOTALS = "W System computes totals for you in POST /einvoice/create; if you build the XML yourself, recompute it from the lines and round each amount to 2 decimals."

# rule -> (what is wrong, business terms, how to fix)
RULES = {
    "BR-01": ("The invoice does not say which standard it follows.", ["BT-24"], "Add the specification identifier (UBL cbc:CustomizationID, e.g. urn:cen.eu:en16931:2017)."),
    "BR-02": ("The invoice number is missing.", ["BT-1"], "Give every invoice a unique invoice number."),
    "BR-03": ("The issue date is missing.", ["BT-2"], "Add the date the invoice was issued, as YYYY-MM-DD."),
    "BR-04": ("The invoice type code is missing.", ["BT-3"], "Add a type code: 380 for an invoice, 381 for a credit note."),
    "BR-05": ("The invoice currency is missing.", ["BT-5"], "Add an ISO 4217 currency code such as EUR."),
    "BR-06": ("The seller's name is missing.", ["BT-27"], "Add the seller's registered legal name."),
    "BR-07": ("The buyer's name is missing.", ["BT-44"], "Add the buyer's registered legal name."),
    "BR-08": ("The seller's postal address is missing.", ["BG-5"], "Add the seller's address with at least the country code."),
    "BR-09": ("The seller's country code is missing.", ["BT-40"], "Add the seller's two-letter ISO country code, such as DE."),
    "BR-10": ("The buyer's postal address is missing.", ["BG-8"], "Add the buyer's address with at least the country code."),
    "BR-11": ("The buyer's country code is missing.", ["BT-55"], "Add the buyer's two-letter ISO country code, such as BE."),
    "BR-12": ("The sum of the line net amounts is missing.", ["BT-106"], _TOTALS),
    "BR-13": ("The invoice total without VAT is missing.", ["BT-109"], _TOTALS),
    "BR-14": ("The invoice total with VAT is missing.", ["BT-112"], _TOTALS),
    "BR-15": ("The amount due for payment is missing.", ["BT-115"], _TOTALS),
    "BR-16": ("The invoice has no lines.", ["BG-25"], "Add at least one invoice line."),
    "BR-21": ("An invoice line has no line ID.", ["BT-126"], "Number every line (1, 2, 3 ...)."),
    "BR-22": ("An invoice line has no quantity.", ["BT-129"], "Add the invoiced quantity to every line."),
    "BR-23": ("An invoice line has no unit of measure.", ["BT-130"], "Add a UN/ECE unit code such as C62 (piece), HUR (hour) or DAY."),
    "BR-24": ("An invoice line has no net amount.", ["BT-131"], "Add the line net amount (quantity x net price, minus line allowances)."),
    "BR-25": ("An invoice line has no item name.", ["BT-153"], "Give every line a short item name or description."),
    "BR-26": ("An invoice line has no net price.", ["BT-146"], "Add the item net price to every line."),
    "BR-27": ("An item net price is negative.", ["BT-146"], "Prices can't be negative: use a positive price with a negative quantity, or an allowance, or a credit note."),
    "BR-CO-03": ("Both a tax point date and a tax point date code are given.", ["BT-7", "BT-8"], "Give only one of them."),
    "BR-CO-04": ("An invoice line has no VAT category.", ["BT-151"], "Give every line a VAT category code (S standard, Z zero, E exempt, AE reverse charge, K intra-EU, G export, O not subject)."),
    "BR-CO-09": ("A VAT ID does not start with a country prefix.", ["BT-31", "BT-48"], "Write VAT IDs with their ISO country prefix, e.g. DE123456789 (Greece uses EL)."),
    "BR-CO-10": ("The sum of the line net amounts does not match the lines.", ["BT-106", "BT-131"], "Set the line total (LineExtensionAmount in LegalMonetaryTotal) to the sum of all line net amounts. " + _TOTALS),
    "BR-CO-13": ("The total without VAT is not line total minus allowances plus charges.", ["BT-109", "BT-106", "BT-107", "BT-108"], "Set the total without VAT (TaxExclusiveAmount) to line total - allowances + charges. " + _TOTALS),
    "BR-CO-14": ("The total VAT is not the sum of the VAT breakdown amounts.", ["BT-110", "BT-117"], "Set the total VAT (TaxAmount) to the sum of the VAT amounts in the breakdown. " + _TOTALS),
    "BR-CO-15": ("The total with VAT is not total without VAT plus VAT.", ["BT-112", "BT-109", "BT-110"], "Set the total with VAT (TaxInclusiveAmount) to total without VAT + total VAT. " + _TOTALS),
    "BR-CO-16": ("The amount due does not add up: it must be total with VAT minus amount paid plus rounding.", ["BT-115", "BT-112", "BT-113", "BT-114"], "Set the amount due (PayableAmount) to total with VAT - amount already paid + rounding. " + _TOTALS),
    "BR-CO-17": ("A VAT amount in the breakdown is not taxable amount x rate.", ["BT-117", "BT-116", "BT-119"], "Recompute each VAT amount as taxable amount x rate / 100, rounded to 2 decimals. " + _TOTALS),
    "BR-CO-18": ("The invoice has no VAT breakdown.", ["BG-23"], "Add one VAT breakdown per VAT category and rate used on the lines."),
    "BR-CO-25": ("An amount is due but there is neither a due date nor payment terms.", ["BT-9", "BT-20"], "Add a due date or payment terms (for example '30 days net')."),
    "BR-CO-26": ("The seller has no VAT ID, legal registration ID or other identifier.", ["BT-29", "BT-30", "BT-31"], "Add the seller's VAT ID (best) or company registration number."),
    "BR-S-01": ("Lines are standard-rated but there is no standard-rate VAT breakdown.", ["BG-23", "BT-118"], _TOTALS),
    "BR-S-02": ("Lines are standard-rated but the seller has no VAT ID.", ["BT-31", "BT-32"], "Add the seller's VAT ID (or tax number / tax representative)."),
    "BR-S-05": ("A standard-rated line has a 0 % VAT rate.", ["BT-152"], "Use the real rate, or the right category: Z (zero-rated), E (exempt) or AE (reverse charge)."),
    "BR-S-08": ("The standard-rate taxable amount does not match the lines at that rate.", ["BT-116", "BT-131"], _TOTALS),
    "BR-S-09": ("The standard-rate VAT amount is not taxable amount x rate.", ["BT-117"], _TOTALS),
    "BR-S-10": ("A standard-rate VAT breakdown has an exemption reason.", ["BT-120", "BT-121"], "Remove the exemption reason: standard-rated VAT is not exempt."),
    "BR-AE-01": ("Reverse-charge lines need exactly one reverse-charge VAT breakdown.", ["BG-23", "BT-118"], _TOTALS),
    "BR-AE-02": ("Reverse charge needs both VAT IDs.", ["BT-31", "BT-48"], "Add the seller's VAT ID and the buyer's VAT ID (check the buyer's in VIES first)."),
    "BR-AE-03": ("Reverse charge needs both VAT IDs.", ["BT-31", "BT-48"], "Add the seller's VAT ID and the buyer's VAT ID."),
    "BR-AE-05": ("A reverse-charge line has a VAT rate other than 0.", ["BT-152"], "Set the VAT rate to 0 on reverse-charge (AE) lines."),
    "BR-AE-10": ("The reverse-charge breakdown has no exemption reason.", ["BT-120", "BT-121"], "Add vat_exemption_code VATEX-EU-AE or the text 'Reverse charge'."),
    "BR-E-10": ("The exempt VAT breakdown has no exemption reason.", ["BT-120", "BT-121"], "Add the exemption reason code (VATEX-...) or text, e.g. the legal article."),
    "BR-IC-02": ("An intra-EU supply needs both VAT IDs.", ["BT-31", "BT-48"], "Add the seller's and buyer's VAT IDs."),
    "BR-IC-10": ("The intra-EU breakdown has no exemption reason.", ["BT-120", "BT-121"], "Add vat_exemption_code VATEX-EU-IC or the text 'Intra-Community supply'."),
    "BR-IC-11": ("An intra-EU supply needs a delivery date or invoicing period.", ["BT-72"], "Add delivery_date."),
    "BR-IC-12": ("An intra-EU supply needs the deliver-to country.", ["BT-80"], "Add the deliver-to country code."),
    "BR-49": ("Payment instructions have no payment means code.", ["BT-81"], "Add a payment means code such as 58 (SEPA credit transfer)."),
    "BR-50": ("A credit transfer has no account number.", ["BT-84"], "Add the payee IBAN."),
    "BR-61": ("A credit transfer has no account number.", ["BT-84"], "Add the payee IBAN when the payment means is 30 or 58."),
    "BR-62": ("The seller's electronic address has no scheme.", ["BT-34"], "Add the scheme (e.g. 0208 Belgium, 0088 GLN, 9930 German VAT) to the seller's endpoint ID."),
    "BR-63": ("The buyer's electronic address has no scheme.", ["BT-49"], "Add the scheme (e.g. 0208 Belgium) to the buyer's endpoint ID."),
    "BR-DE-1": ("XRechnung needs payment instructions.", ["BG-16"], "Add payment details: means code and IBAN (payment.iban)."),
    "BR-DE-2": ("XRechnung needs a seller contact.", ["BG-6"], "Add seller.contact_name, seller.phone and seller.email."),
    "BR-DE-3": ("XRechnung needs the seller's city.", ["BT-37"], "Add the seller's city."),
    "BR-DE-4": ("XRechnung needs the seller's postal code.", ["BT-38"], "Add the seller's postal code."),
    "BR-DE-5": ("XRechnung needs a seller contact name.", ["BT-41"], "Add seller.contact_name (a person or department)."),
    "BR-DE-6": ("XRechnung needs a seller contact phone number.", ["BT-42"], "Add seller.phone."),
    "BR-DE-7": ("XRechnung needs a seller contact e-mail.", ["BT-43"], "Add seller.email."),
    "BR-DE-8": ("XRechnung needs the buyer's city.", ["BT-52"], "Add the buyer's city."),
    "BR-DE-9": ("XRechnung needs the buyer's postal code.", ["BT-53"], "Add the buyer's postal code."),
    "BR-DE-10": ("XRechnung needs the deliver-to city.", ["BT-77"], "Add the deliver-to city, or leave the delivery address out entirely."),
    "BR-DE-11": ("XRechnung needs the deliver-to postal code.", ["BT-78"], "Add the deliver-to postal code, or leave the delivery address out entirely."),
    "BR-DE-13": ("XRechnung allows only one kind of payment instruction per invoice.", ["BG-16"], "Give either credit transfer, card or direct debit, not several."),
    "BR-DE-14": ("XRechnung needs a VAT rate on every VAT breakdown.", ["BT-119"], "Give every line a vat_rate (0 for exempt or reverse charge)."),
    "BR-DE-15": ("XRechnung needs a buyer reference.", ["BT-10"], "Add buyer_reference: the Leitweg-ID for German public buyers, or any agreed reference (e.g. the PO number) for companies."),
    "BR-DE-16": ("XRechnung needs the seller's VAT ID or tax number.", ["BT-31", "BT-32"], "Add seller.vat_id or seller.tax_number."),
    "BR-DE-17": ("The invoice type code is not allowed in XRechnung.", ["BT-3"], "Use 380 (invoice), 381 (credit note), 384 (corrected), 389 (self-billed), 326 (partial) or 875-877."),
    "BR-DE-18": ("The Skonto (cash discount) line in the payment terms is not in the XRechnung format.", ["BT-20"], "Write it as #SKONTO#TAGE=14#PROZENT=2.00# on its own line, ending with a line break."),
    "BR-DE-19": ("The IBAN is not a valid IBAN.", ["BT-84"], "Check the IBAN (country code, length, check digits)."),
    "BR-DE-21": ("The specification identifier is not XRechnung.", ["BT-24"], "Use format 'xrechnung' in POST /einvoice/create, or set CustomizationID to the XRechnung 3.0 value."),
    "BR-DE-23-a": ("A credit transfer is declared but the account details are missing.", ["BG-17"], "Add payment.iban."),
    "BR-DE-26": ("A corrected invoice (type 384) must refer to the invoice it corrects.", ["BT-25"], "Add preceding_invoice with the original invoice number."),
    "BR-DE-TMP-32": ("XRechnung expects a delivery date or invoicing period.", ["BT-72"], "Add delivery_date (or an invoicing period)."),
    "CII-SR-465": ("The seller contact has both a person name and a department name; XRechnung allows only one.", ["BT-41"], "In XRechnung, use either a person name or a department in the contact, not both."),
    "CII-SR-466": ("The buyer contact has both a person name and a department name; XRechnung allows only one.", ["BT-56"], "In XRechnung, use either a person name or a department in the contact, not both."),
    "PEPPOL-EN16931-R010": ("The buyer's electronic address (Peppol ID) is missing.", ["BT-49"], "Add buyer.endpoint_id and endpoint_scheme, or a VAT ID W System can derive it from (look it up with GET /einvoice/route)."),
    "PEPPOL-EN16931-R020": ("The seller's electronic address (Peppol ID) is missing.", ["BT-34"], "Add seller.endpoint_id and endpoint_scheme."),
}


def _field(bts: list[str]) -> tuple[str | None, str | None]:
    names, js = [], []
    for b in bts:
        n, j = BT.get(b, (None, None))
        names.append(f"{n} ({b})" if n else b)
        if j and j not in js:
            js.append(j)
    return (", ".join(names) or None), (", ".join(js) or None)


def explain(err: dict) -> dict:
    """Return {what, field, json_field, fix} for one validation error/warning dict."""
    rid = err.get("rule_id") or ""
    msg = err.get("message") or ""
    if rid in RULES:
        what, bts, fix = RULES[rid]
        field, js = _field(bts)
        return {"what": what, "field": field, "json_field": js, "fix": err.get("fix") if rid.startswith("BR-CL-") and err.get("fix") else fix}
    if rid == "XSD":
        return {"what": "The XML structure is wrong: an element is unknown, in the wrong order or has a bad value.",
                "field": err.get("location"), "json_field": None,
                "fix": "Fix the structure first (the message gives the line); business-rule results may be unreliable until it passes."}
    if rid.startswith("BR-DEC"):
        field, js = _field(err.get("business_terms") or [])
        return {"what": "An amount has more than 2 decimals.", "field": field, "json_field": js, "fix": "Round amounts to 2 decimals."}
    if rid.startswith(("CII-SR-", "CII-DT-")):
        return {"what": "The CII file uses an element or repetition the EN 16931 CII syntax does not allow.", "field": err.get("location"),
                "json_field": None, "fix": "Remove the element, or validate with the profile your recipient expects (e.g. Factur-X EXTENDED)."}
    field, js = _field(err.get("business_terms") or [])
    what = re.sub(r"^\[[^\]]+\]\s*-?\s*", "", msg).strip() or "This rule failed."
    fix = err.get("fix") or (f"Change {field.split(',')[0] if field else 'the field named in the message'} so that this holds: {what}")
    return {"what": what, "field": field or err.get("location"), "json_field": js, "fix": fix}


def annotate(items: list[dict]) -> list[dict]:
    for e in items or []:
        if isinstance(e, dict) and "explain" not in e:
            try:
                e["explain"] = explain(e)
            except Exception:
                pass
    return items
