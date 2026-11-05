INSERT INTO public.booking_types
    (client_id, name, duration_minutes, buffer_minutes, audience, delivery, resource_rule,
     location_kind, address, meeting_link, needs_confirmation, min_notice_minutes,
     max_advance_days, change_cutoff_minutes)
VALUES (%(client_id)s, %(name)s, %(duration_minutes)s, %(buffer_minutes)s, %(audience)s,
        %(delivery)s, %(resource_rule)s, %(location_kind)s, %(address)s, %(meeting_link)s,
        %(needs_confirmation)s, %(min_notice_minutes)s, %(max_advance_days)s,
        %(change_cutoff_minutes)s)
RETURNING id;
