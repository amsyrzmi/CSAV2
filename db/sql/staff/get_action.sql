SELECT a.id, a.client_id, a.target_id, a.order_id, a.conversation_id, a.approval_id, a.action, a.status, a.expires_at,
       t.address, t.is_enabled
FROM public.staff_actions AS a
JOIN public.staff_notification_targets AS t
  ON t.id = a.target_id AND t.client_id = a.client_id
WHERE a.client_id = %s
  AND a.id = %s
