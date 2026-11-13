-- The invoice's reminder rows for staff, oldest first. The address column never leaves here.
SELECT r.step, r.channel, r.status, r.created_at, r.sent_at
FROM public.invoice_reminders r
WHERE r.client_id = %(client_id)s AND r.invoice_id = %(invoice_id)s
ORDER BY r.created_at, r.step;
