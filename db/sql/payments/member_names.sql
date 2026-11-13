-- Display names of the console users in %(user_ids)s who are members of the company or platform
-- admins (who record payments for a company they select). Run on the identity pool, inside tx for
-- the company (csa_app cannot read console_users; the membership policy limits the EXISTS to it).
SELECT u.id AS user_id, u.display_name
FROM public.console_users u
WHERE u.id = ANY(%(user_ids)s::uuid[])
  AND (u.is_platform_admin
       OR EXISTS (SELECT 1 FROM public.console_memberships m
                   WHERE m.client_id = %(client_id)s AND m.user_id = u.id));
