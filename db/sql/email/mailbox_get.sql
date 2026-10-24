SELECT id,address,status,access_token,refresh_token,token_expires_at,scopes,history_id,last_sync_at FROM public.email_mailboxes WHERE client_id=%s AND user_id=%s FOR UPDATE;
