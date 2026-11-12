-- The sign-up being collected in this chat: the newest open draft (a pending_actions row that is
-- never a button; its payload holds the plan, the name and a typed phone, and what was read back).
SELECT id, payload
FROM public.pending_actions
WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s
  AND conversation_id = %(conversation_id)s
  AND agent = 'subscription_agent' AND action = 'signup_draft'
  AND status = 'open' AND expires_at > now()
ORDER BY created_at DESC, id DESC
LIMIT 1;
