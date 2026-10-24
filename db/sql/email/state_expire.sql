DELETE FROM public.email_oauth_states WHERE client_id=%s AND user_id=%s AND expires_at<=clock_timestamp();
