# W System e-invoice validator, free edition: offline CLI. Rules are baked in at build time (checksum-pinned).
FROM python:3.13-slim AS rules
WORKDIR /src
COPY wsystem_einvoice wsystem_einvoice
COPY scripts/fetch_rules.py scripts/fetch_rules.py
RUN python scripts/fetch_rules.py /opt/wsystem/rules

FROM python:3.13-slim
LABEL org.opencontainers.image.title="W System e-invoice validator" \
      org.opencontainers.image.description="Offline EN 16931 / XRechnung validator for UBL, CII and ZUGFeRD/Factur-X (free edition)" \
      org.opencontainers.image.vendor="W System" \
      org.opencontainers.image.licenses="Apache-2.0 AND EUPL-1.2 AND MPL-2.0" \
      org.opencontainers.image.source="https://github.com/wsystemdev/einvoice-validator" \
      org.opencontainers.image.url="https://wsystem.dev"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WSV_RULES_DIR=/opt/wsystem/rules WSV_LICENSES_DIR=/licenses
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE THIRD-PARTY-NOTICES.md ./
COPY wsystem_einvoice wsystem_einvoice
RUN pip install --no-cache-dir . && rm -rf /root/.cache \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin wsv
COPY --from=rules /opt/wsystem/rules /opt/wsystem/rules
COPY licenses /licenses
COPY LICENSE /licenses/LICENSE-W-System-Apache-2.0.txt
COPY NOTICE /licenses/NOTICE.txt
COPY THIRD-PARTY-NOTICES.md /licenses/THIRD-PARTY-NOTICES.md
RUN python -c "import importlib.metadata as m; print('\n'.join(sorted(f\"{d.metadata['Name']} {d.version} {d.metadata.get('License-Expression') or d.metadata.get('License') or ''}\".strip() for d in m.distributions())))" > /licenses/python-packages.txt
USER 10001
WORKDIR /work
ENTRYPOINT ["wsv"]
CMD ["--help"]
