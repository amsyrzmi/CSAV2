-- How many reminders a contact was sent, or has on their way, in one local day
-- [day_start, day_end), over all of its invoices: rows queued, sending or sent. A row that reached
-- nobody does not count: skipped, no_channel, dropped (no longer owed when the mail job checked
-- it) and failed (the send failed; it is never retried).
SELECT count(*) AS reminders
FROM public.invoice_reminders r
JOIN public.invoices i ON i.client_id = r.client_id AND i.id = r.invoice_id
WHERE r.client_id = %(client_id)s AND i.contact_id = %(contact_id)s
  AND r.status IN ('queued', 'sending', 'sent')
  AND r.created_at >= %(day_start)s AND r.created_at < %(day_end)s;
