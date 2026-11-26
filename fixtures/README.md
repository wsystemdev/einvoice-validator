# Fixtures

Expected results: `expected.json` (rule pack 2026.11.1). Shared with the W System PHP SDK so both report the same results.

| Path | Source | Licence |
|---|---|---|
| valid/*_fx.pdf, invalid/MINIMUM_*, invalid/BASIC-WL_* | Mustang project ZUGFeRD 2.x samples | Apache-2.0 |
| valid/KoSIT_* (incl. KoSIT_02.01a-cvd_*, accepted with BR-CL-13 as warning) | KoSIT XRechnung test suite | Apache-2.0 |
| valid/*-BR-CL-23-unit-code-warning.xml | W System (fictional data; unlisted unit code, KoSIT standard scenario level warning) | Apache-2.0 |
| invalid/XRECHNUNG_Einfach_fx.pdf | Mustang project ZUGFeRD 2.x sample; two BT-41, CII-SR-465 is an error in the KoSIT XRechnung scenario (KoSIT rejects it too) | Apache-2.0 |
| valid/ubl-xrechnung-wsystem.xml | W System | Apache-2.0 |
| invalid/ubl-*, invalid/cii-* | W System modifications of the KoSIT samples (one rule broken each) | Apache-2.0 |
| invalid/not-an-invoice.xml, invalid/xxe-doctype.xml | W System (input rejection, XXE guard) | Apache-2.0 |

MINIMUM and BASIC WL are not EN 16931 invoices by design, so they fail the EN 16931 rules.
