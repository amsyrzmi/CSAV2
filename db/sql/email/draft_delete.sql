DELETE FROM public.email_drafts WHERE client_id=%s AND mailbox_id=%s AND id=%s AND status='draft' RETURNING id;
