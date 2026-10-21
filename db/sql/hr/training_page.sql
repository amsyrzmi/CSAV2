SELECT id AS request_id,employee_id,title,learning_objective,status,version,updated_at,
    decision_by,decision_note FROM public.hr_training_requests
WHERE client_id=%s AND (%s::uuid IS NULL OR id>%s::uuid)
AND public.hr_resource_visible(client_id,employee_id,'training_read') ORDER BY id LIMIT %s;
