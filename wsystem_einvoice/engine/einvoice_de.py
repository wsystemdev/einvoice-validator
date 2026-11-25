"""B25: which validation errors touch German VAT-mandatory invoice items (sections 14(4), 14a, 22c UStG).

The BT/BG assignments follow the KoSIT / FeRD / AWV mapping table "Pflichtangaben nach UStG und EN16931"
(published 25 Jul 2025, https://xeinkauf.de/xrechnung/ustg). Only the business-term references are used
(facts), not the table's text. Generic invoice-note groups (BG-1) are not mapped, because almost every
invoice note would match. Not tax advice.
"""
from __future__ import annotations

SOURCE = ("KoSIT / FeRD / AWV mapping table 'Pflichtangaben nach UStG und EN16931' (25 Jul 2025), "
          "https://xeinkauf.de/xrechnung/ustg")
VERSION = "W System DE VAT-item map 2026-10-27 (KoSIT/FeRD table 2025-07-25)"

_N = "§ 14 Abs. 4 Satz 1 Nr. {} UStG"
# (paragraph, item in plain English, conditional?, business terms)
_ITEMS = [
    (_N.format(1), "full name and address of the supplier", False,
     ["BG-4", "BT-27", "BT-28", "BG-5", "BT-35", "BT-36", "BT-162", "BT-37", "BT-38", "BT-39", "BT-40"]),
    (_N.format(1), "full name and address of the recipient", False,
     ["BG-7", "BT-44", "BT-45", "BG-8", "BT-50", "BT-51", "BT-163", "BT-52", "BT-53", "BT-54", "BT-55"]),
    (_N.format(2), "supplier's tax number or VAT ID", False, ["BT-31", "BT-32"]),
    (_N.format(3), "issue date", False, ["BT-2"]),
    (_N.format(4), "invoice number", False, ["BT-1"]),
    (_N.format(5), "quantity and type of goods or services", False, ["BG-25", "BT-129", "BG-31", "BT-153"]),
    (_N.format(6), "date of supply or service period", False, ["BG-13", "BT-72", "BG-14", "BT-73", "BT-74", "BG-26", "BT-134", "BT-135"]),
    (_N.format(6), "date payment was received, if before the issue date", True, ["BT-7"]),
    (_N.format("6a"), "cash-accounting note (Versteuerung nach vereinnahmten Entgelten)", True, ["BT-8"]),
    (_N.format(7), "net amount broken down by VAT rate and exemption, and agreed reductions", False,
     ["BT-131", "BG-27", "BT-136", "BT-139", "BG-28", "BT-141", "BG-29", "BT-146", "BG-20", "BT-92", "BT-97",
      "BG-21", "BT-99", "BG-22", "BT-106", "BT-107", "BT-109", "BT-112", "BT-116"]),
    (_N.format(7), "agreed reductions such as cash discount terms", True, ["BT-20"]),
    (_N.format(8), "VAT rate", False, ["BG-30", "BT-152", "BT-96", "BT-118", "BT-119"]),
    (_N.format(8), "VAT amount", False, ["BG-23", "BT-110", "BT-117"]),
    (_N.format(8), "exemption note", False, ["BT-120", "BT-121"]),
    (_N.format(10), "'Gutschrift' for self-billed invoices", True, ["BT-3"]),
    ("§ 14a UStG", "buyer's VAT ID for reverse-charge, intra-EU or triangular supplies", True, ["BT-48"]),
    ("§ 22c UStG", "fiscal representative's name, address and VAT ID", True, ["BG-11", "BT-62", "BG-12", "BT-63"]),
]

MAP: dict[str, list[dict]] = {}
for para, item, cond, bts in _ITEMS:
    for bt in bts:
        MAP.setdefault(bt, []).append({"provision": para, "item": item, "conditional": cond})


def annotate(errors: list[dict]) -> dict:
    """Add 'de_vat_items' to each error whose business terms map to a VAT-mandatory item; return a summary."""
    hit, hit_uncond, provisions = 0, 0, set()
    for e in errors:
        items, seen = [], set()
        for bt in e.get("business_terms") or []:
            for it in MAP.get(bt, []):
                key = (it["provision"], it["item"])
                if key not in seen:
                    seen.add(key)
                    items.append({"business_term": bt, **it})
        if items:
            e["de_vat_items"] = items
            hit += 1
            provisions.update(i["provision"] for i in items)
            if any(not i["conditional"] for i in items):
                hit_uncond += 1
    unmapped = sum(1 for e in errors if not e.get("business_terms") and e.get("rule_id") != "XSD")
    return {"vat_mandatory_error_count": hit, "vat_mandatory_unconditional_error_count": hit_uncond,
            "vat_mandatory_provisions": sorted(provisions), "errors_without_business_terms": unmapped}
