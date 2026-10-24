UPDATE public.email_mailboxes SET status='connected',address=%s,access_token=%s,refresh_token=COALESCE(%s,refresh_token),token_expires_at=%s,scopes=%s,updated_at=clock_timestamp()
WHERE client_id=%s AND user_id=%s AND id=%s RETURNING id;
