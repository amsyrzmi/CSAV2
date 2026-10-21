SELECT area FROM public.hr_private_grants WHERE client_id=%s AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid ORDER BY area;
