UPDATE public.booking_settings
SET reminder_minutes = %(reminder_minutes)s, pending_hours = %(pending_hours)s,
    customer_limit = %(customer_limit)s, version = version + 1
WHERE client_id = %(client_id)s
RETURNING reminder_minutes, pending_hours, customer_limit, version;
