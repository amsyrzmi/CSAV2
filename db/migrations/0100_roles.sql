-- 0100_roles.sql — roles + grants (LG01 §5.2).
--
-- Idempotent and additive. The login roles are created NOLOGIN with NO password
-- here; LOGIN + password is set by scripts/migrate.py via a parameterized
-- ALTER ROLE reading the value from the environment, so no secret ever lands in
-- a committed .sql file or the schema_migrations ledger.
--
-- Role model:
--   csa     — table owner, migrations, SECURITY DEFINER triggers. Already the DB
--             owner in production; created only IF NOT EXISTS. No BYPASSRLS (the
--             owner's natural non-FORCE bypass is what we rely on).
--   csa_app — runtime role (gateway/worker/delivery). Subject to RLS. DML + the
--             sequence USAGE and function EXECUTE a non-owner INSERT needs.
--   csa_ro  — read-only analytics/tools role. Subject to RLS. SELECT only; no
--             sequence USAGE and no function EXECUTE beyond read.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'csa') THEN
    CREATE ROLE csa NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'csa_app') THEN
    CREATE ROLE csa_app NOLOGIN;   -- no password in SQL, ever
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'csa_ro') THEN
    CREATE ROLE csa_ro NOLOGIN;    -- no password in SQL, ever
  END IF;
END $$;

-- Schema usage.
GRANT USAGE ON SCHEMA public TO csa_app;
GRANT USAGE ON SCHEMA public TO csa_ro;

-- Table DML: csa_app read/write, csa_ro read-only.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO csa_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO csa_ro;

-- Sequence USAGE for csa_app: a non-owner INSERT firing a nextval() default
-- needs USAGE (or UPDATE) on the owning sequence. csa_ro never inserts.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO csa_app;

-- Function EXECUTE for csa_app: the preserved non-SECURITY-DEFINER trigger
-- functions (keep_escalated_status, contact_identity_tenant_check, ...) run as
-- the invoking role csa_app during its own writes.
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO csa_app;

-- Default privileges so future objects created by owner csa stay reachable.
ALTER DEFAULT PRIVILEGES FOR ROLE csa IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO csa_app;
ALTER DEFAULT PRIVILEGES FOR ROLE csa IN SCHEMA public
  GRANT SELECT ON TABLES TO csa_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE csa IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO csa_app;
ALTER DEFAULT PRIVILEGES FOR ROLE csa IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO csa_app;
