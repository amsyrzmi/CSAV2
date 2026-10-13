INSERT INTO public.outbound_delivery_attempts
    (delivery_id, attempt_no, status_code, error, duration_ms)
VALUES (%s, %s, %s, %s, %s);
