-- Claim, release the claim, or assign: the assignee fields only.
UPDATE public.conversations
SET assignee_user_id = %(assignee_user_id)s::uuid,
    claimed_at = CASE WHEN %(assignee_user_id)s::uuid IS NULL THEN NULL ELSE now() END,
    inbox_version = inbox_version + 1
WHERE client_id = %(client_id)s AND id = %(conversation_id)s
RETURNING id;
