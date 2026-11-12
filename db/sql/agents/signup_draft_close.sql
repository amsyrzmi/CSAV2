-- Close this chat's open sign-up drafts: `cancelled` when a stale one is replaced, `used` once the
-- sign-up is filed or answered for good. The typed name and phone are cleared with the status
-- (a typed number is kept only on the sign-up request).
UPDATE public.pending_actions
SET status = %(status)s, payload = '{}'::jsonb
WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s
  AND conversation_id = %(conversation_id)s
  AND agent = 'subscription_agent' AND action = 'signup_draft'
  AND status = 'open';
