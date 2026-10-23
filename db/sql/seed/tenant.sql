SELECT id, slug FROM public.clients WHERE id = %(client_id)s OR slug = %(slug)s;
