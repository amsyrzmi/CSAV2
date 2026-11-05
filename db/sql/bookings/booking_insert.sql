INSERT INTO public.bookings
    (client_id, contact_id, service, status, starts_at, ends_at, type_id, title, kind,
     created_by, source, private, location_kind, address, meeting_link)
VALUES (%(client_id)s, %(contact_id)s, %(service)s, %(status)s, %(starts_at)s, %(ends_at)s,
        %(type_id)s, %(title)s, %(kind)s, %(created_by)s, 'staff', %(private)s,
        %(location_kind)s, %(address)s, %(meeting_link)s)
RETURNING id;
