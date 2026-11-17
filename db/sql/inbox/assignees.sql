-- Who a conversation can be assigned to: the company's enabled administrators and staff whose
-- account is enabled. Read-only members are not listed; no email is selected.
-- Run on the identity pool inside a tenant transaction (csa_app cannot read console_users or
-- console_memberships; the membership policy limits the rows to the company in any case).
SELECT u.id, u.display_name, m.role
FROM public.console_memberships m
JOIN public.console_users u ON u.id = m.user_id
WHERE m.client_id = %(client_id)s
  AND m.is_enabled
  AND u.is_enabled
  AND m.role IN ('tenant_admin', 'tenant_staff')
ORDER BY lower(u.display_name), u.display_name, u.id;
