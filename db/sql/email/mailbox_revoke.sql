UPDATE public.email_mailboxes SET status='revoked',access_token=NULL,refresh_token=NULL,token_expires_at=NULL,scopes='{}',updated_at=clock_timestamp()
WHERE client_id=%s AND user_id=%s RETURNING id;
