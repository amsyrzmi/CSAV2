SELECT id AS request_id, employee_id, title, learning_objective, status, version,
    updated_at, decision_by, decision_note
FROM public.hr_training_requests WHERE client_id=%s AND id=%s FOR UPDATE;
