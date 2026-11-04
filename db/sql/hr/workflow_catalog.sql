SELECT domain,action,scope FROM public.hr_workflow_grants WHERE client_id=%s AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid ORDER BY 1,2,3;
