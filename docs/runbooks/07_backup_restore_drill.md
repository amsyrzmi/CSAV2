# Runbook 07 — Backup and Restore Drill

## Objective
Verify database disaster recovery, restore data into an isolated test instance, and validate Row Level Security (RLS) and schema version integrity.

## Backup Procedure (Host / Server)
```sh
# Nightly compressed database dump excluding owner privileges
pg_dump -h localhost -p 5432 -U csa -d csa -Fc -f "/var/backups/csa_$(date +%Y%m%d_%H%M%S).dump"
```

## Restore Drill Procedure (Isolated Scratch Environment)
Never restore over a live database! Use a scratch database or container:

```sh
# 1. Create a scratch database
createdb -h localhost -p 5433 -U csa csa_restore_drill

# 2. Restore dump into scratch database
pg_restore -h localhost -p 5433 -U csa -d csa_restore_drill --no-owner --clean --if-exists /var/backups/csa_<TIMESTAMP>.dump

# 3. Apply any pending migrations
MIGRATE_DATABASE_URL="postgres://csa:secret@localhost:5433/csa_restore_drill" uv run python -m scripts.migrate
```

## Post-Restore Verification Checklist (SQL)
Run against `csa_restore_drill`:

1. **Verify Schema Version:**
   ```sql
   SELECT max(version) FROM schema_migrations;
   ```
   *Expected:* Matches current `EXPECTED_SCHEMA_VERSION` (e.g. `0250`).

2. **Verify Row Level Security is Enabled:**
   ```sql
   SELECT relname, relrowsecurity
   FROM pg_class
   JOIN pg_namespace n ON n.oid = pg_class.relnamespace
   WHERE n.nspname = 'public'
     AND relname IN ('orders', 'order_items', 'contacts', 'conversations', 'ingress_events');
   ```
   *Expected:* `relrowsecurity = true` for all tenant tables.

3. **Verify LangGraph Checkpoints Schema:**
   ```sql
   SELECT count(*) FROM lg.checkpoints;
   ```
   *Expected:* Table exists and is queryable without error.
