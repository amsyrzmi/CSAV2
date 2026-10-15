-- Read-only schema inventory; run through an existing authorized PostgreSQL connection.
-- Does not print table rows, secrets, message bodies or role password hashes.
\pset pager off
BEGIN READ ONLY;
SET LOCAL statement_timeout = '8s';
SET LOCAL lock_timeout = '2s';

SELECT current_database() AS database_name,
       current_user AS connection_role,
       current_setting('server_version') AS postgres_version,
       current_setting('transaction_read_only') AS read_only_transaction;

SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog','information_schema')
  AND (table_schema = 'app' OR table_name IN
       ('clients','capability_registry','agent_runs','conversations','messages','webhook_endpoints'))
ORDER BY table_schema, table_name;

SELECT table_schema, table_name, ordinal_position, column_name,
       data_type, udt_name, is_nullable
FROM information_schema.columns
WHERE (table_schema = 'app' AND table_name IN
       ('projects','project_channels','users','sessions','audit_log'))
   OR (table_schema NOT IN ('pg_catalog','information_schema') AND table_name IN
       ('clients','capability_registry','agent_runs','conversations','messages','webhook_endpoints'))
ORDER BY table_schema, table_name, ordinal_position;

SELECT n.nspname AS table_schema, r.relname AS table_name,
       c.conname AS constraint_name, c.contype AS constraint_type,
       pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c
JOIN pg_class r ON r.oid = c.conrelid
JOIN pg_namespace n ON n.oid = r.relnamespace
WHERE n.nspname = 'app' AND r.relname IN ('projects','project_channels','users')
ORDER BY table_name, constraint_name;

SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'app' AND tablename IN ('projects','project_channels')
ORDER BY tablename, indexname;

SELECT n.nspname AS schema_name, t.typname AS enum_name,
       e.enumsortorder, e.enumlabel
FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
JOIN pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname = 'app'
ORDER BY schema_name, enum_name, e.enumsortorder;

SELECT table_schema, table_name, privilege_type
FROM information_schema.table_privileges
WHERE grantee = current_user
  AND (table_schema = 'app' OR table_name IN
       ('capability_registry','conversations','messages','clients'))
ORDER BY table_schema, table_name, privilege_type;

SELECT table_schema, table_name, column_name, privilege_type
FROM information_schema.column_privileges
WHERE grantee = current_user
  AND table_name IN ('capability_registry','conversations')
ORDER BY table_schema, table_name, column_name, privilege_type;

COMMIT;
