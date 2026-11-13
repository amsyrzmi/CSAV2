-- The methods a customer may be told to pay with: the company's active QR and transfer methods in
-- the order staff set. Cash is never offered here (Payments 2 pays it through a booking).
SELECT id, kind, label, details, instructions, is_active, sort_order, version
FROM public.payment_methods
WHERE client_id = %(client_id)s
  AND is_active
  AND kind IN ('qr', 'transfer')
ORDER BY sort_order, label, id;
