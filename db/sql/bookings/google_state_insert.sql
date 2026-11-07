INSERT INTO public.booking_calendar_oauth_states
    (state_hash, client_id, user_id, connection_id, code_verifier, expires_at)
VALUES (%(state_hash)s, %(client_id)s, %(user_id)s, %(connection_id)s, %(code_verifier)s,
        now() + make_interval(secs => %(ttl_seconds)s));
