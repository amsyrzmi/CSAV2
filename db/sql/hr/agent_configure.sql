UPDATE public.hr_agent_settings SET enabled=%s WHERE client_id=%s AND name=%s RETURNING name,enabled;
