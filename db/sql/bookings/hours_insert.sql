INSERT INTO public.booking_hours (client_id, subject_kind, subject_id, weekday, opens, closes)
VALUES (%(client_id)s, %(kind)s, %(subject_id)s, %(weekday)s, %(opens)s, %(closes)s);
