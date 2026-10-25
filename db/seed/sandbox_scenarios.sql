-- Repeatable, synthetic scenario data for the isolated development database.
-- Loaded by `make db-reset` after sandbox_clients.sql. Never use with production.

UPDATE public.clients
SET deployment_mode = CASE slug
        WHEN 'sandbox-amsyar' THEN 'suggest'
        WHEN 'sandbox-ihsan' THEN 'auto'
        ELSE deployment_mode
    END,
    brand_tone = CASE slug
        WHEN 'sandbox-amsyar' THEN 'Warm and concise'
        WHEN 'sandbox-ihsan' THEN 'Direct and professional'
        ELSE brand_tone
    END
WHERE slug IN ('sandbox-amsyar', 'sandbox-ihsan');

INSERT INTO public.contacts (id, client_id, telegram_id, full_name, tier, locale)
VALUES
    ('a1000000-0000-4000-8000-000000000001', '11111111-1111-1111-1111-111111111111', 700001001, 'Aina Demo', 'standard', 'en'),
    ('a1000000-0000-4000-8000-000000000002', '11111111-1111-1111-1111-111111111111', 700001002, 'Mei Ling Demo', 'vip', 'zh'),
    ('b2000000-0000-4000-8000-000000000001', '22222222-2222-2222-2222-222222222222', 700002001, 'Ihsan Demo', 'standard', 'ms'),
    ('b2000000-0000-4000-8000-000000000002', '22222222-2222-2222-2222-222222222222', 700002002, 'Arun Demo', 'standard', 'ta')
ON CONFLICT (id) DO UPDATE SET
    telegram_id = EXCLUDED.telegram_id,
    full_name = EXCLUDED.full_name,
    tier = EXCLUDED.tier,
    locale = EXCLUDED.locale;

INSERT INTO public.contact_identities (client_id, channel, external_id, contact_id)
VALUES
    ('11111111-1111-1111-1111-111111111111', 'telegram', '700001001', 'a1000000-0000-4000-8000-000000000001'),
    ('11111111-1111-1111-1111-111111111111', 'telegram', '700001002', 'a1000000-0000-4000-8000-000000000002'),
    ('22222222-2222-2222-2222-222222222222', 'telegram', '700002001', 'b2000000-0000-4000-8000-000000000001'),
    ('22222222-2222-2222-2222-222222222222', 'telegram', '700002002', 'b2000000-0000-4000-8000-000000000002')
ON CONFLICT (client_id, channel, external_id) DO UPDATE SET contact_id = EXCLUDED.contact_id;

INSERT INTO public.conversations (id, client_id, contact_id, channel, channel_thread, summary)
VALUES
    ('a1000000-0000-4000-8000-000000000101', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000001', 'telegram', '700001001', 'Synthetic order and support demo conversation'),
    ('a1000000-0000-4000-8000-000000000102', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000002', 'telegram', '700001002', 'Synthetic VIP customer demo conversation'),
    ('b2000000-0000-4000-8000-000000000101', '22222222-2222-2222-2222-222222222222', 'b2000000-0000-4000-8000-000000000001', 'telegram', '700002001', 'Synthetic order and support demo conversation'),
    ('b2000000-0000-4000-8000-000000000102', '22222222-2222-2222-2222-222222222222', 'b2000000-0000-4000-8000-000000000002', 'telegram', '700002002', 'Synthetic alternate-language demo conversation')
ON CONFLICT (id) DO UPDATE SET summary = EXCLUDED.summary;

INSERT INTO public.products (id, client_id, sku, name, description, category, price, stock_qty, reserved_qty, is_active)
VALUES
    ('a1000000-0000-4000-8000-000000000201', '11111111-1111-1111-1111-111111111111', 'DEMO-PLENTY', 'Demo Everyday Bottle', 'Synthetic item with regular stock.', 'Accessories', 29.90, 20, 0, true),
    ('a1000000-0000-4000-8000-000000000202', '11111111-1111-1111-1111-111111111111', 'DEMO-LOW', 'Demo Limited Pouch', 'Synthetic low-stock item.', 'Accessories', 18.00, 2, 0, true),
    ('a1000000-0000-4000-8000-000000000203', '11111111-1111-1111-1111-111111111111', 'DEMO-OUT', 'Demo Out-of-stock Case', 'Synthetic item with no available stock.', 'Accessories', 45.00, 0, 0, true),
    ('b2000000-0000-4000-8000-000000000201', '22222222-2222-2222-2222-222222222222', 'DEMO-PLENTY', 'Ihsan Demo Tea Set', 'Synthetic item with regular stock.', 'Home', 59.00, 15, 0, true),
    ('b2000000-0000-4000-8000-000000000202', '22222222-2222-2222-2222-222222222222', 'DEMO-LOW', 'Ihsan Demo Gift Box', 'Synthetic low-stock item.', 'Home', 39.00, 1, 0, true),
    ('b2000000-0000-4000-8000-000000000203', '22222222-2222-2222-2222-222222222222', 'DEMO-OUT', 'Ihsan Demo Limited Tin', 'Synthetic item with no available stock.', 'Home', 22.00, 0, 0, true)
ON CONFLICT (client_id, sku) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    category = EXCLUDED.category,
    price = EXCLUDED.price,
    stock_qty = EXCLUDED.stock_qty,
    reserved_qty = EXCLUDED.reserved_qty,
    is_active = EXCLUDED.is_active;

INSERT INTO public.orders (id, client_id, contact_id, status, total, currency, placed_at, shipped_at, tracking_no, carrier, items, channel, payment_status, subtotal)
VALUES
    ('DEMO-A-DRAFT', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000001', 'draft', 29.90, 'MYR', now(), NULL, NULL, NULL, '[{"sku":"DEMO-PLENTY","name":"Demo Everyday Bottle","qty":1,"unit_price":29.90}]', 'telegram', 'unpaid', 29.90),
    ('DEMO-A-PENDING', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000001', 'pending_confirmation', 18.00, 'MYR', now(), NULL, NULL, NULL, '[{"sku":"DEMO-LOW","name":"Demo Limited Pouch","qty":1,"unit_price":18.00}]', 'telegram', 'unpaid', 18.00),
    ('DEMO-A-CONFIRMED', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000001', 'confirmed', 29.90, 'MYR', now() - interval '2 days', NULL, NULL, NULL, '[{"sku":"DEMO-PLENTY","name":"Demo Everyday Bottle","qty":1,"unit_price":29.90}]', 'telegram', 'paid', 29.90),
    ('DEMO-A-PROCESSING', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000001', 'processing', 58.00, 'MYR', now() - interval '3 days', NULL, NULL, NULL, '[{"sku":"DEMO-PLENTY","name":"Demo Everyday Bottle","qty":2,"unit_price":29.00}]', 'telegram', 'paid', 58.00),
    ('DEMO-A-SHIPPED', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000001', 'shipped', 29.90, 'MYR', now() - interval '5 days', now() - interval '3 days', 'SYNTHETIC-TRACK-1001', 'DemoPost', '[{"sku":"DEMO-PLENTY","name":"Demo Everyday Bottle","qty":1,"unit_price":29.90}]', 'telegram', 'paid', 29.90),
    ('DEMO-A-DELIVERED', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000002', 'delivered', 18.00, 'MYR', now() - interval '8 days', now() - interval '6 days', 'SYNTHETIC-TRACK-1002', 'DemoPost', '[{"sku":"DEMO-LOW","name":"Demo Limited Pouch","qty":1,"unit_price":18.00}]', 'telegram', 'paid', 18.00),
    ('DEMO-A-CANCELLED', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000002', 'cancelled', 45.00, 'MYR', now() - interval '10 days', NULL, NULL, NULL, '[{"sku":"DEMO-OUT","name":"Demo Out-of-stock Case","qty":1,"unit_price":45.00}]', 'telegram', 'refunded', 45.00),
    ('DEMO-A-REFUNDED', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000002', 'refunded', 45.00, 'MYR', now() - interval '12 days', NULL, NULL, NULL, '[{"sku":"DEMO-OUT","name":"Demo Out-of-stock Case","qty":1,"unit_price":45.00}]', 'telegram', 'refunded', 45.00),
    ('DEMO-B-SHIPPED', '22222222-2222-2222-2222-222222222222', 'b2000000-0000-4000-8000-000000000001', 'shipped', 59.00, 'MYR', now() - interval '4 days', now() - interval '2 days', 'SYNTHETIC-TRACK-2001', 'DemoPost', '[{"sku":"DEMO-PLENTY","name":"Ihsan Demo Tea Set","qty":1,"unit_price":59.00}]', 'telegram', 'paid', 59.00)
ON CONFLICT (id) DO UPDATE SET
    client_id = EXCLUDED.client_id,
    contact_id = EXCLUDED.contact_id,
    status = EXCLUDED.status,
    total = EXCLUDED.total,
    currency = EXCLUDED.currency,
    placed_at = EXCLUDED.placed_at,
    shipped_at = EXCLUDED.shipped_at,
    tracking_no = EXCLUDED.tracking_no,
    carrier = EXCLUDED.carrier,
    items = EXCLUDED.items,
    channel = EXCLUDED.channel,
    payment_status = EXCLUDED.payment_status,
    subtotal = EXCLUDED.subtotal;

INSERT INTO public.tickets (id, client_id, conversation_id, contact_id, subject, category, priority, status, assignee, sla_due_at)
VALUES
    (970001, '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000101', 'a1000000-0000-4000-8000-000000000001', 'Synthetic delivery status question', 'shipping', 'normal', 'open', 'demo-support', now() + interval '1 day'),
    (970002, '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000102', 'a1000000-0000-4000-8000-000000000002', 'Synthetic refund follow-up', 'refund', 'high', 'open', 'demo-support', now() + interval '4 hours'),
    (970003, '22222222-2222-2222-2222-222222222222', 'b2000000-0000-4000-8000-000000000101', 'b2000000-0000-4000-8000-000000000001', 'Synthetic product question', 'product', 'normal', 'open', 'demo-ops', now() + interval '1 day')
ON CONFLICT (id) DO UPDATE SET
    client_id = EXCLUDED.client_id,
    conversation_id = EXCLUDED.conversation_id,
    contact_id = EXCLUDED.contact_id,
    subject = EXCLUDED.subject,
    category = EXCLUDED.category,
    priority = EXCLUDED.priority,
    status = EXCLUDED.status,
    assignee = EXCLUDED.assignee,
    sla_due_at = EXCLUDED.sla_due_at;

SELECT setval('public.tickets_id_seq', GREATEST((SELECT last_value FROM public.tickets_id_seq), 970003), true);

INSERT INTO public.bookings (id, client_id, contact_id, service, resource, status, starts_at, ends_at, notes)
VALUES
    ('a1000000-0000-4000-8000-000000000301', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-4000-8000-000000000001', 'Synthetic product consultation', 'Demo Room A', 'confirmed', date_trunc('day', now()) + interval '2 days 10 hours', date_trunc('day', now()) + interval '2 days 11 hours', 'Synthetic booking fixture.'),
    ('b2000000-0000-4000-8000-000000000301', '22222222-2222-2222-2222-222222222222', 'b2000000-0000-4000-8000-000000000001', 'Synthetic tea tasting', 'Demo Room B', 'confirmed', date_trunc('day', now()) + interval '3 days 14 hours', date_trunc('day', now()) + interval '3 days 15 hours', 'Synthetic booking fixture.')
ON CONFLICT (id) DO UPDATE SET
    client_id = EXCLUDED.client_id,
    contact_id = EXCLUDED.contact_id,
    service = EXCLUDED.service,
    resource = EXCLUDED.resource,
    status = EXCLUDED.status,
    starts_at = EXCLUDED.starts_at,
    ends_at = EXCLUDED.ends_at,
    notes = EXCLUDED.notes;

INSERT INTO public.kb_gaps (id, client_id, query, hits, resolved)
VALUES
    ('a1000000-0000-4000-8000-000000000401', '11111111-1111-1111-1111-111111111111', 'Synthetic question with no matching return-policy article', 3, false),
    ('b2000000-0000-4000-8000-000000000401', '22222222-2222-2222-2222-222222222222', 'Synthetic question about an unavailable product option', 1, false)
ON CONFLICT (id) DO UPDATE SET query = EXCLUDED.query, hits = EXCLUDED.hits, resolved = EXCLUDED.resolved;
