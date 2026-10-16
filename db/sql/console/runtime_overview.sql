SELECT count(*) AS runs, count(*) FILTER (WHERE ok) AS succeeded,
       count(*) FILTER (WHERE NOT ok) AS failed, max(created_at) AS last_run_at
FROM public.agent_runs
WHERE client_id = %s AND created_at >= now() - interval '24 hours';
