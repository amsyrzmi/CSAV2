UPDATE public.staff_actions
SET status = 'open'
WHERE client_id = %s
  AND target_id = (SELECT target_id FROM public.staff_actions WHERE client_id = %s AND id = %s)
  AND action = 'edit_reply'
  AND status = 'awaiting'
  AND id <> %s;
