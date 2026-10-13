INSERT INTO public.dlq (client_id, trace_id, stage, envelope, error)
VALUES (%s, %s, %s, %s, %s);
