-- A company's payment methods in the order staff set, optionally only the active ones.
SELECT id, kind, label, details, instructions, is_active, sort_order, version
FROM public.payment_methods
WHERE client_id = %(client_id)s
  AND (NOT %(active_only)s OR is_active)
ORDER BY sort_order, label, id;
