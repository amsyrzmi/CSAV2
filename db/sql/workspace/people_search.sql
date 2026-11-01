SELECT employee_id, display_name, job_title, work_email
FROM public.company_directory d
WHERE client_id = %s
  -- Every word: anywhere in the name or work email, or at a word start in the job title (so a
  -- surname such as "Tan" is not found inside "Assistant").
  AND NOT EXISTS (
    SELECT 1 FROM unnest(%s::text[], %s::text[]) AS w(p, t)
    WHERE NOT (
      coalesce(d.display_name, '') ILIKE w.p
      OR coalesce(d.job_title, '') ~* w.t
      OR coalesce(d.work_email, '') ILIKE w.p))
-- People whose name or work email holds every word come before title-only matches.
ORDER BY EXISTS (
    SELECT 1 FROM unnest(%s::text[]) AS w(p)
    WHERE NOT (coalesce(d.display_name, '') ILIKE w.p OR coalesce(d.work_email, '') ILIKE w.p)),
  display_name, employee_id
LIMIT 10
