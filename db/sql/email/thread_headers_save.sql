-- Threading headers read from Gmail for a row synced before 0484; kept only if the latest message is unchanged.
UPDATE public.email_threads SET latest_rfc_message_id=%s,latest_references=%s,latest_reply_to=%s,latest_from_owner=%s
WHERE client_id=%s AND mailbox_id=%s AND thread_id=%s AND latest_message_id IS NOT DISTINCT FROM %s;
