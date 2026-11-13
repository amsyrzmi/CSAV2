SELECT id, kind, label, details, instructions, is_active, sort_order, version
FROM public.payment_methods
WHERE client_id = %(client_id)s AND id = %(method_id)s;
