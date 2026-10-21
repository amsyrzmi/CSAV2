INSERT INTO public.console_workspace_turns
    (client_id, thread_id, owner_user_id, session_id, request_key, fingerprint, body,
     action, page_key, resource_id, result)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id, request_key, body, result, created_at;
