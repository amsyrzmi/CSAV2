INSERT INTO public.hr_client_owners(client_id,user_id,approved_by,approval_reference)
SELECT m.client_id,m.user_id,%s::uuid,%s FROM public.console_memberships m
JOIN public.console_users u ON u.id=m.user_id WHERE m.client_id=%s AND m.user_id=%s
AND m.is_enabled AND u.is_enabled AND NOT u.must_change_password
AND NOT EXISTS(SELECT 1 FROM public.hr_client_owners WHERE client_id=m.client_id)
RETURNING client_id;
