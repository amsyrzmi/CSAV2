UPDATE public.messages SET send_authority = 'reviewed' WHERE client_id = %s AND id = %s;
