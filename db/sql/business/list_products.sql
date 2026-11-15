SELECT p.id, p.sku, p.name, p.price, p.currency, p.stock_qty, p.reserved_qty, p.available_qty,
       p.is_active, p.created_at, p.updated_at,
       CASE WHEN a.id IS NULL THEN NULL
            ELSE jsonb_build_object('id', a.id, 'version', a.sha256) END AS picture
FROM public.products p
LEFT JOIN public.media_assets a
    ON a.client_id = p.client_id AND a.kind = 'product' AND a.subject_key = p.id::text
WHERE p.client_id = %s
  AND (%s::uuid IS NULL OR (p.created_at, p.id) <
      (SELECT created_at, id FROM public.products WHERE client_id = %s AND id = %s))
ORDER BY p.created_at DESC, p.id DESC LIMIT %s;
