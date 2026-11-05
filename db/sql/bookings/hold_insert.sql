INSERT INTO public.booking_holds (client_id, booking_id, subject_kind, subject_id, during)
VALUES (%(client_id)s, %(booking_id)s, %(subject_kind)s, %(subject_id)s,
        tstzrange(%(starts_at)s, %(ends_at)s));
