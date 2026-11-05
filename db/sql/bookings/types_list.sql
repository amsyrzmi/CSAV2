SELECT t.id, t.name, t.duration_minutes, t.buffer_minutes, t.audience, t.delivery,
       t.resource_rule, t.location_kind, t.address, t.meeting_link, t.needs_confirmation,
       t.min_notice_minutes, t.max_advance_days, t.change_cutoff_minutes, t.is_active, t.version,
       ARRAY(SELECT p.person_id FROM public.booking_type_people p
              WHERE p.client_id = t.client_id AND p.type_id = t.id ORDER BY p.person_id)
           AS person_ids,
       ARRAY(SELECT r.resource_id FROM public.booking_type_resources r
              WHERE r.client_id = t.client_id AND r.type_id = t.id ORDER BY r.resource_id)
           AS resource_ids
FROM public.booking_types t
WHERE t.client_id = %(client_id)s AND (%(id)s::uuid IS NULL OR t.id = %(id)s::uuid)
ORDER BY lower(t.name), t.id;
