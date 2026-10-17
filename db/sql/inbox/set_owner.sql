UPDATE public.conversations
SET owned_by = %s, status = %s, ai_muted_until = NULL,
    ai_suppressed_before = clock_timestamp(), inbox_version = inbox_version + 1
WHERE client_id = %s AND id = %s;
