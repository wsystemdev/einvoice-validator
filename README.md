# W System e-invoice validator (free edition)

Validate UBL, CII, XRechnung and ZUGFeRD / Factur-X e-invoices against the official **CEN EN 16931** and **KoSIT XRechnung 3.0** rules,
**fully offline**: the rules are baked into the image, nothing is downloaded at runtime and no invoice data leaves your machine.

```bash
docker run --rm --network none -v "$PWD:/work" ghcr.io/wsystemdev/einvoice-validator:0.1 validate invoices/
```

```
INVALID  invoices/2026-0042.xml  (XRechnung, UBL, 1 errors, 0 warnings)
  error line 12: BR-DE-15: Das Element "Buyer reference" (BT-10) muss übermittelt werden. [BT-10] Fix: ...
```

- Formats: UBL 2.1 Invoice / CreditNote, UN/CEFACT CII D16B, XRechnung 3.0 (UBL + CII, incl. Extension), ZUGFeRD 2.x / Factur-X
  (the embedded XML is extracted from the PDF and validated).
- Rules: rule pack `2026.11.1` = KoSIT XRechnung validator configuration 3.0.2 (2026-08-31) with CEN EN 16931 Schematron 1.3.16 and
  XRechnung Schematron 2.4.0, used unmodified. Peppol BIS Billing 3.0 rules are not included. `wsv rules` shows the installed pack.
- Output: `--format text|json|github` (GitHub annotations with line numbers), `--summary FILE` (Markdown table, e.g. `$GITHUB_STEP_SUMMARY`),
  `--json-out FILE`, `--fail-on-warnings`.
- Exit codes: `0` all valid, `1` at least one invalid or unreadable invoice, `2` usage error / no files matched, `3` rule files missing.

In GitHub Actions use [wsystemdev/einvoice-validate-action](https://github.com/wsystemdev/einvoice-validate-action).

## Without Docker

```bash
pip install .
python scripts/fetch_rules.py        # one-time, checksum-pinned download of the official rule files into ./rules
wsv validate invoices/
```

## Self-hosted validator (commercial)

For production pipelines W System also offers a self-hosted validator with a local HTTP API, signed rule-pack updates, several rule
versions side by side, PDF/A-3 container checks and HTML/JUnit/SARIF reports: see https://wsystem.dev .

## Licence

W System code: Apache-2.0 (`LICENSE`). Third-party rule artefacts keep their own licences (CEN EN 16931: EUPL-1.2, unmodified; KoSIT:
Apache-2.0; Saxon-HE: MPL-2.0): see `NOTICE`, `THIRD-PARTY-NOTICES.md` and `licenses/`, or run `wsv licenses`.

This is a technical check against the official rule sets. A technically valid file is not tax or legal advice.
