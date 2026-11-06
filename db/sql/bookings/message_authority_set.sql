-- Who stands behind a message: decides whether delivery still sends it while a person has taken
-- over the conversation (only `automation` and `handoff` are held back then).
UPDATE public.messages SET send_authority = %(authority)s
WHERE client_id = %(client_id)s AND id = %(id)s;
