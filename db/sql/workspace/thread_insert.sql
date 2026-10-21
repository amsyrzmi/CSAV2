INSERT INTO public.console_workspace_threads
    (client_id, owner_user_id, create_request_key)
VALUES (%s, %s, %s)
RETURNING id, sequence, created_at;
