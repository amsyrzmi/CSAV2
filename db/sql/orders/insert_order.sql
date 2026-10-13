INSERT INTO public.orders
    (id, client_id, contact_id, status, total, currency, channel, payment_status, idempotency_key)
VALUES (%s, %s, %s, 'draft', %s, %s, %s, 'unpaid', %s)
ON CONFLICT (id) DO NOTHING
RETURNING id;
