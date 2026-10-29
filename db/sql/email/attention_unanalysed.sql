-- Candidates for the attention summary that have no analysis yet: newest first, never the owner's
-- own latest message, and only threads whose body is stored.
SELECT thread_id,subject,sender,received_at,latest_body,latest_message_id FROM public.email_threads
WHERE client_id=%s AND mailbox_id=%s AND analysed_at IS NULL AND latest_body IS NOT NULL
 AND latest_from_owner IS NOT TRUE
ORDER BY received_at DESC NULLS LAST,thread_id LIMIT %s;
