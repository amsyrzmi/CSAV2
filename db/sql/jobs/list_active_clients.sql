-- db/sql/jobs/list_active_clients.sql
SELECT id, slug, name, config
FROM clients
WHERE status IN ('pilot', 'active')
ORDER BY created_at;
