SELECT id,display_name,consent_id,retain_until,version FROM public.hr_candidates WHERE client_id=%s AND erased_at IS NULL AND retain_until>%s ORDER BY id LIMIT 200;
