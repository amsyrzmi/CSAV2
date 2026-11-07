-- A connection attempt is used once, by the member who started it, before it runs out.
DELETE FROM public.booking_calendar_oauth_states
WHERE client_id = %(client_id)s AND user_id = %(user_id)s AND state_hash = %(state_hash)s
  AND expires_at > now()
RETURNING connection_id, code_verifier;
