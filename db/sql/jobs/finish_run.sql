/* db/sql/jobs/finish_run.sql */
UPDATE job_runs
SET finished_at = %s,
    ok = %s,
    stats = %s,
    error = %s
WHERE id = %s;
