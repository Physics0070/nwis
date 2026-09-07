# PostgreSQL with the three extensions NWIS targets.
# TimescaleDB publishes images that already bundle PostGIS; pgvector is added on top.
FROM timescale/timescaledb-ha:pg16

# timescaledb-ha already bundles pgvector and PostGIS. The install below is a fallback
# for tags that do not, and is deliberately best-effort: failing the whole image build
# because an apt package is already satisfied would be absurd. Whether the extension is
# really present is proven by init/01-extensions.sql at first start, and reported by
# /api/status at runtime.
USER root
RUN set -eux; \
    apt-get update || true; \
    apt-get install -y --no-install-recommends postgresql-16-pgvector || \
      echo "pgvector not installed from apt; relying on the base image"; \
    rm -rf /var/lib/apt/lists/*
USER postgres
