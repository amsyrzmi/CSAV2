-- Display names of the people holding queue items, for the list and detail reads only (never used
-- to validate an assignee). Named parameters: client_id, user_ids.
-- A member is named whether or not their membership or account is still enabled, so an item held
-- by someone switched off still shows who holds it; platform admins (who can take over for the
-- company they select) are named too. Run on the identity pool inside tx for the company: csa_app
-- cannot read console_users, and the membership policy limits the EXISTS to the company.
SELECT u.id AS user_id, u.display_name
FROM public.console_users u
WHERE u.id = ANY(%(user_ids)s::uuid[])
  AND (u.is_platform_admin
       OR EXISTS (SELECT 1 FROM public.console_memberships m
                  WHERE m.client_id = %(client_id)s AND m.user_id = u.id));
