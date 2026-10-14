UPDATE public.staff_actions
SET status = 'awaiting',
    source_message_id = %s
WHERE client_id = %s
  AND id = %s
  AND status = 'open'
  AND action = 'edit_reply'
RETURNING id;
