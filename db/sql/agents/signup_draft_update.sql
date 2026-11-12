-- Update this chat's one open sign-up draft in place with what the turn collected, and start its
-- hour again from this save. No row comes back when the chat has no readable open draft.
UPDATE public.pending_actions
SET payload = %(payload)s, expires_at = now() + %(life)s
WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s
  AND conversation_id = %(conversation_id)s
  AND agent = 'subscription_agent' AND action = 'signup_draft'
  AND status = 'open' AND expires_at > now()
RETURNING id;
