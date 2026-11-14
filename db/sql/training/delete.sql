-- Any item in any state. A video's pieces, job, transcript and windows go with it (cascade);
-- the usage row has no foreign key and stays.
DELETE FROM public.training_items
WHERE client_id = %s AND id = %s
RETURNING id, kind, storage_name, byte_size;
