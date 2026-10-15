SELECT i.id,
       i.client_id,
       i.contact_id,
       i.order_id,
       i.status,
       i.amount,
       i.currency,
       i.issued_at,
       i.due_at,
       i.paid_at,
       i.payment_ref,
       i.line_items,
       c.email,
       c.telegram_id,
       c.full_name
FROM public.invoices i
JOIN public.contacts c ON c.id = i.contact_id
WHERE i.client_id = %s
  AND (i.id = %s OR i.order_id = %s)
  AND i.contact_id = %s
LIMIT 1;
