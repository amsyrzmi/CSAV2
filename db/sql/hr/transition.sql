UPDATE public.hr_training_requests SET status=%s,version=%s,updated_at=%s,
    decision_by=%s,decision_note=%s
WHERE client_id=%s AND id=%s AND version=%s
RETURNING id AS request_id, employee_id, title, learning_objective, status, version,
    updated_at, decision_by, decision_note;
