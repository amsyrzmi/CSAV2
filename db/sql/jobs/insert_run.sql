/* db/sql/jobs/insert_run.sql */
INSERT INTO job_runs (job, client_id, started_at, ok, stats)
VALUES (%s, %s, %s, NULL, '{}'::jsonb)
RETURNING id;
