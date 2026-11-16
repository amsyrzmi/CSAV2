-- The methods a customer may be told to pay with: the company's first active QR and transfer
-- methods in the order staff set, as many as the limit (CUSTOMER_METHODS in csa/payments/copy.py,
-- four; the reminder job takes the same ones), so a reply stays one chat message. Cash is never
-- offered here (Payments 2 pays it through a booking). Each comes with its picture (a QR method's
-- uploaded image, joined within the company as payments/methods_list.sql does) or NULL; the
-- payment agent sends the first listed QR's picture (csa/payments/pictures.py).
SELECT m.id, m.kind, m.label, m.details, m.instructions, m.is_active, m.sort_order, m.version,
       CASE WHEN a.id IS NULL THEN NULL
            ELSE jsonb_build_object('id', a.id, 'version', a.sha256) END AS picture
FROM public.payment_methods m
LEFT JOIN public.media_assets a
    ON a.client_id = m.client_id AND a.kind = 'payment_qr' AND a.subject_key = m.id::text
WHERE m.client_id = %(client_id)s
  AND m.is_active
  AND m.kind IN ('qr', 'transfer')
ORDER BY m.sort_order, m.label, m.id
LIMIT %(limit)s;
