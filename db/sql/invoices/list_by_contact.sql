SELECT id, client_id, contact_id, order_id, status, amount, currency, issued_at, due_at, paid_at, payment_ref, line_items
FROM public.invoices
WHERE client_id = %s
  AND contact_id = %s
ORDER BY issued_at DESC
LIMIT %s;
