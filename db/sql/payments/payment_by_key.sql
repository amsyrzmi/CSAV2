-- The payment a request key already made (idempotency_key is unique per company), with what that
-- payment did to its subscription, so a replay gives the first answer: activated (an 'activated'
-- event) and resumed (a 'resumed_paid' event) whose payload names this payment.
SELECT p.id, p.invoice_id,
       EXISTS (SELECT 1 FROM public.subscription_events e
                WHERE e.client_id = p.client_id AND e.subscription_id = i.subscription_id
                  AND e.action = 'activated' AND e.payload ->> 'payment_id' = p.id::text)
           AS activated,
       EXISTS (SELECT 1 FROM public.subscription_events e
                WHERE e.client_id = p.client_id AND e.subscription_id = i.subscription_id
                  AND e.action = 'resumed_paid' AND e.payload ->> 'payment_id' = p.id::text)
           AS resumed
FROM public.payments p
LEFT JOIN public.invoices i ON i.client_id = p.client_id AND i.id = p.invoice_id
WHERE p.client_id = %(client_id)s AND p.idempotency_key = %(idempotency_key)s;
