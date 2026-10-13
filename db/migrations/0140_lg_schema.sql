-- 0140_lg_schema.sql — LangGraph checkpoint schema + grants (LG01 §7.1).
--
-- Creates the dedicated `lg` schema owned by csa and grants csa_app the access
-- the AsyncPostgresSaver checkpoint tables need. This is the ONLY place lg
-- privileges are granted: 0100_roles.sql cannot grant on schema lg because lg
-- does not exist when it runs.
--
-- Ordering authority (design §7.1): this migration runs via scripts/migrate.py
-- BEFORE scripts/lg_setup.py creates the lg.* tables. The ALTER DEFAULT
-- PRIVILEGES below is therefore in place first, so the checkpoint tables that
-- saver.setup() creates as owner csa inherit the csa_app grant at creation
-- time — no post-setup "GRANT ON ALL TABLES" step is needed.
--
-- 0140 is the highest auto-applied .sql migration, so EXPECTED_SCHEMA_VERSION
-- for LG01 is 0140. lg.* tables have no client_id column, so RLS is never
-- enabled there and csa_app's DML on them is unfiltered (design §7.2).
--
-- Idempotent and additive.

CREATE SCHEMA IF NOT EXISTS lg AUTHORIZATION csa;

-- csa_app must be able to enter the schema to reach its tables.
GRANT USAGE ON SCHEMA lg TO csa_app;

-- Set BEFORE saver.setup() runs so the checkpoint tables created by owner csa
-- are automatically reachable by csa_app.
ALTER DEFAULT PRIVILEGES FOR ROLE csa IN SCHEMA lg
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO csa_app;
