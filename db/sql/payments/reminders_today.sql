-- How many reminders a contact was sent or queued in one local day [day_start, day_end), over all
-- of its invoices. skipped and no_channel rows reached nobody and do not count.
SELECT count(*) AS reminders
FROM public.invoice_reminders r
JOIN public.invoices i ON i.client_id = r.client_id AND i.id = r.invoice_id
WHERE r.client_id = %(client_id)s AND i.contact_id = %(contact_id)s
  AND r.status NOT IN ('skipped', 'no_channel')
  AND r.created_at >= %(day_start)s AND r.created_at < %(day_end)s;
