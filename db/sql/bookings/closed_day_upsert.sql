INSERT INTO public.booking_closed_days (client_id, day, note)
VALUES (%(client_id)s, %(day)s, %(note)s)
ON CONFLICT (client_id, day) DO UPDATE SET note = EXCLUDED.note
RETURNING day, note;
