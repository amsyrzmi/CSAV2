-- Read under the company lock, so two uploads cannot both take the last slot.
SELECT count(*)::int AS items, coalesce(sum(byte_size), 0)::bigint AS bytes
FROM public.training_items
WHERE client_id = %s;
