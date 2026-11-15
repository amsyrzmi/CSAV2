-- A company's payment methods in the order staff set, optionally only the active ones, each with
-- its picture (a QR method's uploaded image) or NULL.
SELECT m.id, m.kind, m.label, m.details, m.instructions, m.is_active, m.sort_order, m.version,
       CASE WHEN a.id IS NULL THEN NULL
            ELSE jsonb_build_object('id', a.id, 'version', a.sha256) END AS picture
FROM public.payment_methods m
LEFT JOIN public.media_assets a
    ON a.client_id = m.client_id AND a.kind = 'payment_qr' AND a.subject_key = m.id::text
WHERE m.client_id = %(client_id)s
  AND (NOT %(active_only)s OR m.is_active)
ORDER BY m.sort_order, m.label, m.id;
