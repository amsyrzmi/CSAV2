-- One active product of this company, by SKU: the product detail answer. has_picture: the company
-- has a picture for this product (media_assets, kind 'product', keyed by the product id as text);
-- an EXISTS, so a product without one asks for none and the row is never multiplied. Only an
-- active product gives a row at all, so an archived product never asks for a picture (delivery
-- also sends a product picture only while the product is active, media/send_read.sql). The detail
-- answer sends it (csa/agents/orders/agent.py product_picture).
SELECT p.id, p.sku, p.name, p.description, p.price, p.currency, p.available_qty, p.unit,
       EXISTS (
           SELECT 1 FROM public.media_assets a
           WHERE a.client_id = %(client_id)s AND a.kind = 'product' AND a.subject_key = p.id::text
       ) AS has_picture
FROM public.products p
WHERE p.client_id = %(client_id)s
  AND p.is_active
  AND p.sku = %(sku)s;
