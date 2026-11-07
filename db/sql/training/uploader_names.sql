-- Run on the identity pool (csa_app cannot read console_users) inside tx for the company, so the
-- membership policy limits it to that company's members. A platform admin has no membership and so
-- no name here.
SELECT m.user_id, u.display_name
FROM public.console_memberships m
JOIN public.console_users u ON u.id = m.user_id
WHERE m.client_id = %s AND m.user_id = ANY(%s::uuid[]);
