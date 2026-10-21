UPDATE public.hr_cases SET status=%s,version=version+1,updated_at=%s,decision_reason=%s
WHERE client_id=%s AND id=%s AND version=%s RETURNING *;
