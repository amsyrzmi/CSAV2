-- Active products of this company by name, SKU or category: the product search and the
-- availability answer (csa/agents/orders/agent.py _search, _availability). has_picture: the
-- company has a picture for this product (media_assets, kind 'product', keyed by the product id as
-- text), the same EXISTS as get_product.sql, so a row is never multiplied. Only the availability
-- answer with exactly one row asks for that picture; the search never does.
SELECT p.id, p.sku, p.name, p.price, p.currency, p.available_qty,
       EXISTS (
           SELECT 1 FROM public.media_assets a
           WHERE a.client_id = %(client_id)s AND a.kind = 'product' AND a.subject_key = p.id::text
       ) AS has_picture
FROM public.products p
WHERE p.client_id = %(client_id)s
  AND p.is_active
  AND (p.name ILIKE %(pattern)s OR p.sku ILIKE %(pattern)s
       OR coalesce(p.category, '') ILIKE %(pattern)s)
ORDER BY p.name
LIMIT 5;
