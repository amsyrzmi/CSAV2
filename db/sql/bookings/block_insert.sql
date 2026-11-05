INSERT INTO public.booking_blocks
    (client_id, subject_kind, subject_id, starts_at, ends_at, note, created_by)
VALUES (%(client_id)s, %(subject_kind)s, %(subject_id)s, %(starts_at)s, %(ends_at)s, %(note)s,
        %(created_by)s)
RETURNING id, subject_kind, subject_id, starts_at, ends_at, note, created_by;
