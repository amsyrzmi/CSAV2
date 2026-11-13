-- The methods a customer may be told to pay with: the company's first active QR and transfer
-- methods in the order staff set, as many as the limit (CUSTOMER_METHODS in csa/payments/copy.py,
-- four; the reminder job takes the same ones), so a reply stays one chat message. Cash is never
-- offered here (Payments 2 pays it through a booking).
SELECT id, kind, label, details, instructions, is_active, sort_order, version
FROM public.payment_methods
WHERE client_id = %(client_id)s
  AND is_active
  AND kind IN ('qr', 'transfer')
ORDER BY sort_order, label, id
LIMIT %(limit)s;
