# Third-party components

| Component | Version | Licence | Where |
|---|---|---|---|
| CEN EN 16931 validation artefacts | 1.3.16 | EUPL-1.2 (unmodified) | rules/ (inside the KoSIT config) |
| KoSIT XRechnung validator configuration | 3.0.2 / 2026-08-31 | Apache-2.0 | rules/ |
| KoSIT XRechnung Schematron | 2.4.0 | Apache-2.0 | rules/ |
| Saxon-HE (saxonche) | 13.1.1 | MPL-2.0 | Python package |
| lxml | 6.1.3 | BSD-3-Clause | Python package |
| pypdf | 7.1.1 | BSD-3-Clause | Python package |
| httpx | 0.28.1 | BSD-3-Clause | Python package (imported by the engine; no network use at runtime) |
| Python | 3.13 | PSF-2.0 | base image |
| Debian (python:3.13-slim base image) | trixie | various (see /usr/share/doc/*/copyright in the image) | base image |

The image also contains `/licenses/python-packages.txt`, generated at build time from the installed packages' metadata.
