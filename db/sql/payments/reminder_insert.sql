-- One reminder row for an invoice step. UNIQUE (client_id, invoice_id, step) makes a step once:
-- on conflict nothing is written and no row comes back, which the caller reads as already done.
-- address is set for an email row only, lower case; sent_at for a chat message written now.
INSERT INTO public.invoice_reminders
    (client_id, invoice_id, step, channel, address, status, created_at, sent_at)
VALUES (%(client_id)s, %(invoice_id)s, %(step)s, %(channel)s, %(address)s, %(status)s,
        %(created_at)s, %(sent_at)s)
ON CONFLICT (client_id, invoice_id, step) DO NOTHING
RETURNING id;
