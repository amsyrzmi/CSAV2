WITH active_people AS MATERIALIZED (SELECT p.employee_id FROM public.hr_people_rows(%s) p WHERE p.active)
SELECT count(*)::integer AS n
FROM public.hr_training_requests r
JOIN public.hr_employees e ON e.client_id=r.client_id AND e.id=r.employee_id AND e.is_active
WHERE r.client_id=%s AND r.status='submitted'
AND e.manager_id IN (SELECT employee_id FROM active_people)
AND EXISTS(SELECT 1 FROM public.hr_grants g LEFT JOIN public.hr_employee_accounts a ON a.client_id=g.client_id AND a.user_id=g.user_id
 WHERE g.client_id=r.client_id AND g.user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid
 AND g.action='training.decide' AND ((g.scope='client_owner' AND public.hr_is_owner(g.client_id))
 OR (g.scope='assigned_manager' AND a.employee_id=e.manager_id AND (a.employee_id<>r.employee_id OR public.hr_is_owner(g.client_id)))));
