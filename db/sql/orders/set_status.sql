UPDATE public.orders
SET status = %s,
    payment_status = %s,
    placed_at = CASE WHEN %s THEN coalesce(placed_at, now()) ELSE placed_at END,
    confirmed_at = CASE WHEN %s THEN coalesce(confirmed_at, now()) ELSE confirmed_at END,
    cancelled_at = CASE WHEN %s THEN now() ELSE cancelled_at END,
    updated_at = now()
WHERE client_id = %s
  AND id = %s
RETURNING id;
