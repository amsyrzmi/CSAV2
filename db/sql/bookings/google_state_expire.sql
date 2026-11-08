-- Sweeps a member's connection attempts that ran out of time.
DELETE FROM public.booking_calendar_oauth_states
WHERE client_id = %(client_id)s AND user_id = %(user_id)s AND expires_at <= now();
