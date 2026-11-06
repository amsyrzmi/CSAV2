INSERT INTO public.booking_settings (client_id, reminder_minutes, pending_hours, customer_limit)
VALUES (%(client_id)s, %(reminder_minutes)s, %(pending_hours)s, %(customer_limit)s)
RETURNING reminder_minutes, pending_hours, customer_limit, version;
