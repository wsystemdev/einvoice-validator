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

## Limits and timeouts (0.1.1.rc1)

The validator applies the same protections as the W System API, with the same defaults. Every file is checked against
size, line and element caps before it is parsed, and the rule engine runs in a separate worker process that is stopped
after a time limit. A file that hits a limit is reported as `not checked` with an `error_code` (exit code 1); the next
file is checked normally. Set the limits with environment variables, e.g. `docker run -e EINVOICE_MAX_LINES=5000 ...`:

| Variable | Default | Meaning |
|---|---|---|
| `EINVOICE_MAX_BYTES` | `5242880` (5 MB) | Maximum file size, and maximum size of the XML embedded in a PDF (`payload_too_large`) |
| `EINVOICE_MAX_LINES` | `2000` | Maximum invoice lines (`too_many_lines`) |
| `EINVOICE_MAX_ELEMENTS` | `60000` | Maximum XML elements (`too_many_elements`) |
| `EINVOICE_TIMEOUT_S` | `20` | Time limit for the rule engine per file, in seconds (`validation_timeout`) |
| `EINVOICE_REQUEST_BUDGET_S` | `30` | Total time per file including waiting for the worker, in seconds |
| `EINVOICE_WORKER` | `1` | `0` runs the rule engine in-process: faster start, but no time limit |
| `EINVOICE_WORKER_READY_S` | `120` | Maximum start-up time of the worker (it compiles the rule sets once) |
| `EINVOICE_WORKER_RECYCLE_MB` | `300` | After a reply, a worker above this RSS (MiB) is replaced (0 = never) |

Raising the caps lets bigger invoices through but makes each check slower; keep a time limit when you validate files from
untrusted sources.

## Changes in 0.1.1.rc1

- Engine and worker resynced from the W System API 0.3.157.1 (`6abc6da`): KoSIT standard/CVD severity levels, the 0.3.155.2
  worker recycle, the CII-SR-465/466 fix hint, safe env parsing, and the 0.3.157 PDF extract wording.
- **Fix hint:** in standard XRechnung, CII-SR-465 (seller contact) and CII-SR-466 (buyer contact) now say
  "In XRechnung, use either a person name or a department in the contact, not both."
- Non-numeric `EINVOICE_*` env values (e.g. `EINVOICE_MAX_LINES=abc`) fall back to the default with a warning, instead of a traceback.
- File paths that contain a comma are no longer split into two arguments.
- Limits and timeouts above, including worker recycle (the protections of the W System API 0.3.155.2 / 0.3.157.1).

## Changes in 0.1.1.dev2

- Severity levels of the official KoSIT XRechnung scenarios (`customLevel` in `scenarios.xml`) are now applied for standard
  XRechnung and XRechnung CVD, as KoSIT does, not only for XRechnung Extension. The official CVD samples (BR-CL-13) and unit
  codes outside the BR-CL-23 list in standard XRechnung are now accepted with a warning.
- **Stricter, matching KoSIT:** in standard XRechnung, CII-SR-452, CII-SR-453, CII-SR-454, CII-SR-465, CII-SR-466 and (UBL invoices)
  UBL-CR-646 now count as errors, so files that break them now fail. Example: Mustang's `XRECHNUNG_Einfach_fx.pdf` (two BT-41
  elements, CII-SR-465), which the KoSIT validator rejects too.
- Limits and timeouts (the protections of the W System API 0.3.155.1).

## Without Docker

```bash
pip install .
wsv fetch-rules rules              # one-time, checksum-pinned download of the official rule files
export WSV_RULES_DIR=$PWD/rules
wsv validate invoices/
```

## Self-hosted validator (commercial)

For production pipelines W System also offers a self-hosted validator with a local HTTP API, signed rule-pack updates, several rule
versions side by side, PDF/A-3 container checks and HTML/JUnit/SARIF reports: see https://wsystem.dev .

## Licence

W System code: Apache-2.0 (`LICENSE`). Third-party rule artefacts keep their own licences (CEN EN 16931: EUPL-1.2, unmodified; KoSIT:
Apache-2.0; Saxon-HE: MPL-2.0): see `NOTICE`, `THIRD-PARTY-NOTICES.md` and `licenses/`, or run `wsv licenses`.

This is a technical check against the official rule sets. A technically valid file is not tax or legal advice.
