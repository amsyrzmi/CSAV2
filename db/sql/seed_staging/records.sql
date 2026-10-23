-- Synthetic business records for the staging demo tenants. Additive and
-- idempotent: every statement skips rows that already exist. Fixed identifiers
-- match scripts/seed_staging.py. No KB vectors, deliveries, provider tokens,
-- approvals or runtime traces are fabricated here.

INSERT INTO public.products
    (id, client_id, sku, name, description, category, brand, price,
     stock_qty, reserved_qty, is_active)
SELECT
    ('b1000000-0000-4000-8000-' || lpad(n::text, 12, '0'))::uuid,
    'a1000000-0000-4000-8000-000000000001'::uuid,
    sku, name, 'Synthetic demo item; not a real offer',
    'Electronics accessories', 'Meranti Lab', price, stock, reserved, active
FROM (VALUES
    (1, 'MGL-CABLE-1', 'USB-C Cable 1m', 19.00, 40, 3, true),
    (2, 'MGL-CHARGER-30', 'USB-C Charger 30W', 59.00, 15, 2, true),
    (3, 'MGL-POWER-10', 'Power Bank 10000mAh', 89.00, 0, 0, true),
    (4, 'MGL-EARBUDS', 'Wireless Earbuds', 129.00, 4, 1, true),
    (5, 'MGL-HUB-6', 'USB-C Hub 6-in-1', 119.00, 2, 2, true),
    (6, 'MGL-STAND', 'Adjustable Phone Stand', 29.00, 25, 0, true),
    (7, 'MGL-CASE-OLD', 'Retired Phone Case', 15.00, 6, 0, false),
    (8, 'MGL-CLOTH', 'Microfibre Cloth', 5.00, 100, 0, true)
) AS item(n, sku, name, price, stock, reserved, active)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.products (id, client_id, sku, name, price, stock_qty)
VALUES
    ('b2000000-0000-4000-8000-000000000001',
     'a1000000-0000-4000-8000-000000000002',
     'FOREIGN-ONLY', 'FOREIGN TENANT MARKER - MUST NOT LEAK', 999.00, 1)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.contacts (id, client_id, full_name, email, locale)
VALUES
    ('c1000000-0000-4000-8000-000000000001',
     'a1000000-0000-4000-8000-000000000001',
     'Demo Aina', 'aina@example.test', 'en'),
    ('c1000000-0000-4000-8000-000000000002',
     'a1000000-0000-4000-8000-000000000001',
     'Demo Farid', 'farid@example.test', 'ms'),
    ('c2000000-0000-4000-8000-000000000001',
     'a1000000-0000-4000-8000-000000000002',
     'Foreign Demo Customer', 'foreign@example.test', 'en')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.conversations
    (id, client_id, contact_id, channel, channel_thread, summary)
VALUES
    ('d1000000-0000-4000-8000-000000000001',
     'a1000000-0000-4000-8000-000000000001',
     'c1000000-0000-4000-8000-000000000001',
     'telegram', 'synthetic-history-aina', 'Synthetic order and delivery questions'),
    ('d1000000-0000-4000-8000-000000000002',
     'a1000000-0000-4000-8000-000000000001',
     'c1000000-0000-4000-8000-000000000002',
     'telegram', 'synthetic-history-farid', 'Synthetic Malay-language return question'),
    ('d2000000-0000-4000-8000-000000000001',
     'a1000000-0000-4000-8000-000000000002',
     'c2000000-0000-4000-8000-000000000001',
     'telegram', 'synthetic-history-foreign', 'FOREIGN TRANSCRIPT MUST NOT LEAK')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.messages
    (client_id, conversation_id, direction, author, body, idempotency_key)
VALUES
    ('a1000000-0000-4000-8000-000000000001',
     'd1000000-0000-4000-8000-000000000001', 'inbound', 'customer',
     'Where is order MGL-TEST-005? Contact aina@example.test.',
     'staging-seed-v1:aina:in'),
    ('a1000000-0000-4000-8000-000000000001',
     'd1000000-0000-4000-8000-000000000001', 'outbound', 'assistant',
     'Synthetic historical message only; no Telegram send occurred.',
     'staging-seed-v1:aina:out'),
    ('a1000000-0000-4000-8000-000000000001',
     'd1000000-0000-4000-8000-000000000002', 'inbound', 'customer',
     'Bolehkah saya pulangkan pengecas yang belum dibuka?',
     'staging-seed-v1:farid:in'),
    ('a1000000-0000-4000-8000-000000000002',
     'd2000000-0000-4000-8000-000000000001', 'inbound', 'customer',
     'FOREIGN TRANSCRIPT MUST NOT LEAK', 'staging-seed-v1:foreign:in')
ON CONFLICT (idempotency_key) DO NOTHING;

INSERT INTO public.orders
    (id, client_id, contact_id, status, total, subtotal, shipping_fee,
     channel, external_shop_id, payment_status, placed_at, tracking_no, carrier)
SELECT
    'MGL-TEST-' || lpad(n::text, 3, '0'),
    'a1000000-0000-4000-8000-000000000001'::uuid,
    'c1000000-0000-4000-8000-000000000001'::uuid,
    status, 67.00, 59.00, 8.00, channel, shop, payment,
    now() - n * interval '1 day',
    CASE WHEN status IN ('shipped', 'delivered') THEN 'SYNTHETIC-TRACK-' || n END,
    CASE WHEN status IN ('shipped', 'delivered') THEN 'Synthetic Carrier' END
FROM (VALUES
    (1, 'draft', 'manual', NULL, 'unpaid'),
    (2, 'pending_confirmation', 'telegram', NULL, 'unpaid'),
    (3, 'confirmed', 'web', NULL, 'paid'),
    (4, 'processing', 'tiktok_shop', 'DEMO-SHOP-A', 'paid'),
    (5, 'shipped', 'shopee', 'DEMO-SHOP-A', 'paid'),
    (6, 'delivered', 'manual', NULL, 'paid'),
    (7, 'cancelled', 'telegram', NULL, 'unpaid'),
    (8, 'refunded', 'web', NULL, 'refunded')
) AS order_case(n, status, channel, shop, payment)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.order_items
    (client_id, order_id, product_id, sku, name_snapshot, unit_price, qty)
SELECT o.client_id, o.id, 'b1000000-0000-4000-8000-000000000002'::uuid,
    'MGL-CHARGER-30', 'USB-C Charger 30W', 59.00, 1
FROM public.orders o
WHERE o.client_id = 'a1000000-0000-4000-8000-000000000001'
  AND o.id LIKE 'MGL-TEST-%'
  AND NOT EXISTS (
      SELECT 1 FROM public.order_items i
      WHERE i.client_id = o.client_id AND i.order_id = o.id AND i.sku = 'MGL-CHARGER-30');

INSERT INTO public.orders (id, client_id, contact_id, status, total)
VALUES ('FOREIGN-TEST-001', 'a1000000-0000-4000-8000-000000000002',
    'c2000000-0000-4000-8000-000000000001', 'confirmed', 999.00)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.tickets
    (client_id, conversation_id, contact_id, subject, category, priority, status)
SELECT v.client_id::uuid, v.conversation_id::uuid, v.contact_id::uuid,
       v.subject, v.category, v.priority, v.status
FROM (VALUES
    ('a1000000-0000-4000-8000-000000000001',
     'd1000000-0000-4000-8000-000000000001',
     'c1000000-0000-4000-8000-000000000001',
     'Synthetic delayed delivery enquiry', 'delivery', 'normal', 'open'),
    ('a1000000-0000-4000-8000-000000000001',
     'd1000000-0000-4000-8000-000000000002',
     'c1000000-0000-4000-8000-000000000002',
     'Synthetic unopened charger return', 'returns', 'normal', 'closed')
) AS v(client_id, conversation_id, contact_id, subject, category, priority, status)
WHERE NOT EXISTS (
    SELECT 1 FROM public.tickets t
    WHERE t.client_id = v.client_id::uuid AND t.subject = v.subject);

INSERT INTO public.invoices
    (id, client_id, contact_id, order_id, status, amount, issued_at, due_at)
VALUES
    ('MGL-INV-001', 'a1000000-0000-4000-8000-000000000001',
     'c1000000-0000-4000-8000-000000000001', 'MGL-TEST-003',
     'paid', 67.00, now() - interval '3 days', now() - interval '1 day'),
    ('MGL-INV-002', 'a1000000-0000-4000-8000-000000000001',
     'c1000000-0000-4000-8000-000000000001', 'MGL-TEST-002',
     'issued', 67.00, now() - interval '4 days', now() - interval '2 days')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.kb_gaps (client_id, query, hits)
VALUES
    ('a1000000-0000-4000-8000-000000000001',
     'Do you offer repairs for water-damaged laptops?', 3),
    ('a1000000-0000-4000-8000-000000000001',
     'Can I buy a monthly accessory subscription?', 2)
ON CONFLICT (client_id, query_hash) DO NOTHING;
