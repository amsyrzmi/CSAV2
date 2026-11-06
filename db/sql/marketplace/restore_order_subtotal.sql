-- The order_items trigger rewrites subtotal as the sum of the lines (zero when none are left);
-- the mapped subtotal (TikTok's, or the sum of the folded lines) is the final word.
UPDATE public.orders SET subtotal = %(subtotal)s
WHERE client_id = %(client_id)s AND id = %(order_id)s;
