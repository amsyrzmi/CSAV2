SELECT count(*) AS threads FROM public.email_threads WHERE client_id=%s AND mailbox_id=%s;
