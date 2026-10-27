SELECT a.id, a.approval_id
FROM public.staff_actions AS a
JOIN public.staff_notification_targets AS t
  ON t.id = a.target_id AND t.client_id = a.client_id
WHERE a.client_id = %s
  AND a.action = 'edit_reply'
  AND a.status = 'awaiting'
  AND (%s::bigint IS NULL OR a.source_message_id = %s::bigint)
  AND a.expires_at > now()
  AND t.is_enabled
  AND t.channel IN ('telegram','whatsapp')
  AND t.address = %s
ORDER BY a.created_at DESC
LIMIT 1;
