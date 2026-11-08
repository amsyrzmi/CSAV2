-- Which live companies (pilot or active, as for every other job) have email reminders to work on:
-- queued rows, or rows lost mid-send (`sending`, queued before %(lost_before)s). The one whose
-- queued row has waited longest comes first. Run as the owner, which is all the mail job uses it
-- for: every read and write for a company then goes through that company's own transaction, as
-- `csa_app`, with row security on. A company that is not live keeps its rows queued until it is.
SELECT r.client_id,
       bool_or(r.status = 'sending') AS lost
FROM public.booking_reminders r
JOIN public.clients c ON c.id = r.client_id
WHERE r.channel = 'email'
  AND c.status IN ('pilot', 'active')
  AND (r.status = 'queued' OR (r.status = 'sending' AND r.created_at < %(lost_before)s))
GROUP BY r.client_id
ORDER BY min(r.created_at) FILTER (WHERE r.status = 'queued') NULLS LAST, r.client_id;
