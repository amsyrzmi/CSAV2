UPDATE public.messages SET send_authority = 'handoff' WHERE client_id = %s AND id = %s;
