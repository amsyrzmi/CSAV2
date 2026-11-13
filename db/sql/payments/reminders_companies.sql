-- Which live companies (pilot or active, as for every other job) have payment reminder email rows
-- to work on: queued rows, while the company's payments are on (payment_settings.enabled and a
-- subscriptions package in %(package_statuses)s), or rows lost mid-send (`sending`, queued before
-- %(lost_before)s) whatever the switches say. `queued` says whether it has rows to send; the time
-- zone is for quiet hours. The one whose queued row has waited longest comes first. Run as the
-- owner, which is all the mail job uses it for: every read and write for a company then goes
-- through that company's own transaction, as `csa_app`, with row security on. A company that is not
-- live, or whose payments are off, keeps its rows queued until it is live and on again.
SELECT r.client_id,
       c.timezone,
       bool_or(r.status = 'sending') AS lost,
       bool_or(r.status = 'queued') AS queued
FROM public.invoice_reminders r
JOIN public.clients c ON c.id = r.client_id
WHERE r.channel = 'email'
  AND c.status IN ('pilot', 'active')
  AND ((r.status = 'queued'
        AND EXISTS (SELECT 1 FROM public.payment_settings s
                     WHERE s.client_id = r.client_id AND s.enabled)
        AND EXISTS (SELECT 1 FROM public.console_service_packages p
                     WHERE p.client_id = r.client_id AND p.package = ANY(%(package_keys)s)
                       AND p.status = ANY(%(package_statuses)s)))
       OR (r.status = 'sending' AND r.created_at < %(lost_before)s))
GROUP BY r.client_id, c.timezone
ORDER BY min(r.created_at) FILTER (WHERE r.status = 'queued') NULLS LAST, r.client_id;
