-- Close this chat's open sign-up drafts: `cancelled` when a newer one replaces them, `used` once
-- the sign-up is filed or answered for good.
UPDATE public.pending_actions
SET status = %(status)s
WHERE client_id = %(client_id)s AND contact_id = %(contact_id)s
  AND conversation_id = %(conversation_id)s
  AND agent = 'subscription_agent' AND action = 'signup_draft'
  AND status = 'open';
