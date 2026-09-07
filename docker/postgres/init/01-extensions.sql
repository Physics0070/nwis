-- Enable the extensions NWIS uses. The backend detects which are present at startup and
-- reports them through /api/status, so a missing one degrades rather than breaks.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
