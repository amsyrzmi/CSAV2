INSERT INTO public.hr_notifications(client_id,subject_id,id,reminder_key,kind,resource_id)
SELECT r.client_id,%s,gen_random_uuid(),r.id::text || ':' || %s,'training_review',r.id
FROM public.hr_training_requests r CROSS JOIN LATERAL public.hr_lock_employee(r.client_id,r.employee_id) e
WHERE r.client_id=%s AND r.status='submitted' AND r.updated_at<=%s AND e.manager_active
AND EXISTS(SELECT 1 FROM public.hr_grants g LEFT JOIN public.hr_employee_accounts a ON a.client_id=g.client_id AND a.user_id=g.user_id
 WHERE g.client_id=r.client_id AND g.user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid
 AND g.action='training.decide' AND ((g.scope='client_owner' AND public.hr_is_owner(g.client_id))
 OR (g.scope='assigned_manager' AND a.employee_id=e.manager_id AND (a.employee_id<>r.employee_id OR public.hr_is_owner(g.client_id)))))
ON CONFLICT(client_id,subject_id,reminder_key) DO NOTHING;
