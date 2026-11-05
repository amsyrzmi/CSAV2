UPDATE public.booking_types
SET name = %(name)s, duration_minutes = %(duration_minutes)s, buffer_minutes = %(buffer_minutes)s,
    audience = %(audience)s, delivery = %(delivery)s, resource_rule = %(resource_rule)s,
    location_kind = %(location_kind)s, address = %(address)s, meeting_link = %(meeting_link)s,
    needs_confirmation = %(needs_confirmation)s, min_notice_minutes = %(min_notice_minutes)s,
    max_advance_days = %(max_advance_days)s, change_cutoff_minutes = %(change_cutoff_minutes)s,
    is_active = %(is_active)s, version = version + 1
WHERE client_id = %(client_id)s AND id = %(id)s;
