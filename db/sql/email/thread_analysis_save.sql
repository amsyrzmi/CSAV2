UPDATE public.email_threads SET category=%s,priority=%s,summary=%s,action_items=%s,analysed_at=clock_timestamp()
WHERE client_id=%s AND mailbox_id=%s AND thread_id=%s;
