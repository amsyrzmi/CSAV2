DELETE FROM public.training_items
WHERE client_id = %s AND id = %s
RETURNING id, storage_name, byte_size;
