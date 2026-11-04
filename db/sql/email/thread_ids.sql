-- Ids of the threads cached for one mailbox (at most 200 after cleanup); load more skips them.
SELECT thread_id FROM public.email_threads WHERE client_id=%s AND mailbox_id=%s;
