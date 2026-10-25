INSERT INTO public.invoices
    (id, client_id, contact_id, status, amount, currency, issued_at, due_at, paid_at, line_items)
SELECT 'DEMO-' || %(prefix)s || '-INV-' || upper(status), %(client_id)s, %(contact_id)s,
    status, 59.90, 'MYR', now() - interval '10 days',
    CASE WHEN status = 'overdue' THEN now() - interval '2 days' ELSE now() + interval '20 days' END,
    CASE WHEN status = 'paid' THEN now() - interval '1 day' ELSE NULL END,
    '[{"description":"Synthetic demonstration invoice","quantity":1,"unit_price":59.90}]'::jsonb
FROM unnest(ARRAY['issued','paid','overdue','void']) AS status
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.plans (client_id, code, name, price, features, sort_order)
VALUES
    (%(client_id)s, 'demo-basic', 'Synthetic Basic', 29.90, '["Synthetic monthly plan"]', 1),
    (%(client_id)s, 'demo-plus', 'Synthetic Plus', 59.90, '["Synthetic extended plan"]', 2)
ON CONFLICT (client_id, code) DO NOTHING;

INSERT INTO public.subscriptions
    (id, client_id, contact_id, plan, status, price, started_at, renews_at, paused_at, cancelled_at)
SELECT public.uuid_generate_v5(%(client_id)s, 'seed:subscription:' || status),
    %(client_id)s, %(contact_id)s, 'demo-basic', status, 29.90, now() - interval '20 days',
    CASE WHEN status = 'active' THEN now() + interval '10 days' ELSE NULL END,
    CASE WHEN status = 'paused' THEN now() - interval '2 days' ELSE NULL END,
    CASE WHEN status = 'cancelled' THEN now() - interval '2 days' ELSE NULL END
FROM unnest(ARRAY['active','paused','cancelled']) AS status
ON CONFLICT (id) DO NOTHING;

SELECT public.seed_preparation_capabilities(%(client_id)s);
