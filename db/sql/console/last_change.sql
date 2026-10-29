-- Newest matching audit row per target through the 0502 definer function (tenant-filtered there).
SELECT t.target, l.action, l.at, l.actor_name, l.details
FROM unnest(%s::text[]) AS t(target)
CROSS JOIN LATERAL public.console_last_change(%s, t.target, %s::text[]) AS l;
