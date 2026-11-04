-- Bookings setup: the Admin's "add my team" and "is this account a member" read.
-- Adds public.booking_team_members(p_client): one row per enabled member of the company with the
--   display name and email of the account and the HR person linked to it, if there is one.
--   csa_app cannot read console_users or console_memberships (0270), and the HR link table
--   hr_employee_accounts shows a caller only their own row (0380, hr_account_self), so the read
--   goes through this narrow SECURITY DEFINER function, in the style of 0502_console_last_change.sql.
--   It runs as the owner csa, filters by tenant itself and raises when p_client is not the
--   transaction's app.client_id. A company without the HR package simply has no link rows, so
--   employee_id is NULL. It returns no password, session or HR data beyond the linked person's id.
-- No tables, policies or roles change. FORCE RLS stays off. EXECUTE goes to csa_app only.
-- Pre-check: 0270 (console_users, console_memberships) and 0380 (hr_employee_accounts) applied;
--   nothing from 0520 onward is used. Safe to run twice: CREATE OR REPLACE FUNCTION, and
--   ALTER OWNER, REVOKE and GRANT are idempotent.
-- Rollback: DROP FUNCTION public.booking_team_members(uuid).

CREATE OR REPLACE FUNCTION public.booking_team_members(p_client uuid)
RETURNS TABLE(user_id uuid, display_name text, email text, employee_id uuid)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF p_client IS DISTINCT FROM nullif(pg_catalog.current_setting('app.client_id', true), '')::uuid
    THEN
        RAISE EXCEPTION 'booking_team_members: not this company' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
    SELECT u.id, u.display_name, u.email, a.employee_id
      FROM public.console_memberships m
      JOIN public.console_users u ON u.id = m.user_id
      LEFT JOIN public.hr_employee_accounts a
        ON a.client_id = m.client_id AND a.user_id = m.user_id
     WHERE m.client_id = p_client AND m.is_enabled AND u.is_enabled
     ORDER BY lower(u.display_name), u.id;
END $$;

ALTER FUNCTION public.booking_team_members(uuid) OWNER TO csa;
REVOKE ALL ON FUNCTION public.booking_team_members(uuid) FROM PUBLIC, csa_ro, csa_identity;
GRANT EXECUTE ON FUNCTION public.booking_team_members(uuid) TO csa_app;
