UPDATE public.hr_notifications SET read_at=coalesce(read_at,%s) WHERE client_id=%s AND id=%s RETURNING id;
