INSERT INTO public.booking_holds (client_id, block_id, subject_kind, subject_id, during)
VALUES (%(client_id)s, %(block_id)s, %(subject_kind)s, %(subject_id)s,
        tstzrange(%(starts_at)s, %(ends_at)s));
