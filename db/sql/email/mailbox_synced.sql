UPDATE public.email_mailboxes SET last_sync_at=clock_timestamp(),updated_at=clock_timestamp() WHERE client_id=%s AND id=%s;
