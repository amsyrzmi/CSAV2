-- The company's queued payment reminder email rows, oldest first. A list of ids only: a row is
-- claimed one at a time, when its turn to send comes (reminder_claim_one.sql).
SELECT id
FROM public.invoice_reminders
WHERE client_id = %(client_id)s AND channel = 'email' AND status = 'queued'
ORDER BY created_at, id
LIMIT %(batch_size)s;
