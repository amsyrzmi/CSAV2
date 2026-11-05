UPDATE public.booking_resources
SET name = %(name)s, capacity = %(capacity)s, is_active = %(is_active)s, version = version + 1
WHERE client_id = %(client_id)s AND id = %(id)s
RETURNING id, name, capacity, is_active, version;
