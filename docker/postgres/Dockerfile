# PostgreSQL with the three extensions NWIS targets.
# TimescaleDB publishes images that already bundle PostGIS; pgvector is added on top.
FROM timescale/timescaledb-ha:pg16

USER root
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-16-pgvector; \
    rm -rf /var/lib/apt/lists/*
USER postgres
