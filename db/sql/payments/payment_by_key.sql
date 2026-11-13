-- The payment a request key already made (idempotency_key is unique per company), with what that
-- payment did to its subscription, so a replay gives the first answer: activated (an 'activated'
-- event), resumed (a 'resumed_paid' event) and recorded_only (a 'payment_recorded' event, the
-- subscription was cancelled and left alone) whose payload names this payment.
SELECT p.id, p.invoice_id,
       EXISTS (SELECT 1 FROM public.subscription_events e
                WHERE e.client_id = p.client_id AND e.subscription_id = i.subscription_id
                  AND e.action = 'activated' AND e.payload ->> 'payment_id' = p.id::text)
           AS activated,
       EXISTS (SELECT 1 FROM public.subscription_events e
                WHERE e.client_id = p.client_id AND e.subscription_id = i.subscription_id
                  AND e.action = 'resumed_paid' AND e.payload ->> 'payment_id' = p.id::text)
           AS resumed,
       EXISTS (SELECT 1 FROM public.subscription_events e
                WHERE e.client_id = p.client_id AND e.subscription_id = i.subscription_id
                  AND e.action = 'payment_recorded' AND e.payload ->> 'payment_id' = p.id::text)
           AS recorded_only
FROM public.payments p
LEFT JOIN public.invoices i ON i.client_id = p.client_id AND i.id = p.invoice_id
WHERE p.client_id = %(client_id)s AND p.idempotency_key = %(idempotency_key)s;
