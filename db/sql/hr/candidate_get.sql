SELECT * FROM public.hr_candidates WHERE client_id=%s AND id=%s AND erased_at IS NULL AND retain_until>%s FOR SHARE;
