-- HR access page (B2): the owner's read side for the "Who can do what" page.
-- Adds two owner-only read functions:
--   public.hr_owner_access_summary(p_client): one row per enabled member with their saved grants,
--     workflow grants and private areas, the employee record linked to their account (if any), the
--     number of active people reporting to that employee, and whether they are the client owner.
--     No row cap: the page pages and searches the result itself.
--   public.hr_owner_reporting_lines(p_client): one row per active employee with their manager's
--     employee id and the manager's enabled console account (NULL when the manager has none).
-- Both are SECURITY DEFINER, pinned to search_path, and return nothing unless hr_is_owner(p_client).
-- No new tables, roles or policies. EXECUTE goes to csa_app only.
-- Pre-check: 0410 applied (hr_is_owner), plus 0380 and 0400 (hr_grants, hr_workflow_grants).
--   Safe to run twice: CREATE OR REPLACE FUNCTION, and REVOKE/GRANT are idempotent.
-- Rollback: DROP FUNCTION public.hr_owner_reporting_lines(uuid);
--   DROP FUNCTION public.hr_owner_access_summary(uuid).

CREATE OR REPLACE FUNCTION public.hr_owner_access_summary(p_client uuid) RETURNS TABLE(
    user_id uuid, display_name text, grants jsonb, workflows jsonb, areas jsonb,
    employee_id uuid, department text, manager_id uuid, active boolean,
    team_size integer, is_owner boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF NOT public.hr_is_owner(p_client) THEN RETURN; END IF;
 RETURN QUERY
 SELECT u.id, u.display_name,
  COALESCE((SELECT jsonb_agg(jsonb_build_object('action',g.action,'scope',g.scope,'fields',to_jsonb(g.fields))
            ORDER BY g.action,g.scope)
            FROM public.hr_grants g WHERE g.client_id=p_client AND g.user_id=u.id),'[]'::jsonb),
  COALESCE((SELECT jsonb_agg(jsonb_build_object('domain',w.domain,'action',w.action,'scope',w.scope)
            ORDER BY w.domain,w.action,w.scope)
            FROM public.hr_workflow_grants w WHERE w.client_id=p_client AND w.user_id=u.id),'[]'::jsonb),
  COALESCE((SELECT jsonb_agg(pg.area ORDER BY pg.area)
            FROM public.hr_private_grants pg WHERE pg.client_id=p_client AND pg.user_id=u.id),'[]'::jsonb),
  e.id, e.department, e.manager_id, e.is_active,
  CASE WHEN e.id IS NULL THEN 0 ELSE
   (SELECT count(*)::integer FROM public.hr_employees r
    WHERE r.client_id=p_client AND r.manager_id=e.id AND r.is_active) END,
  EXISTS(SELECT 1 FROM public.hr_client_owners o WHERE o.client_id=p_client AND o.user_id=u.id)
 FROM public.console_users u
 JOIN public.console_memberships m ON m.user_id=u.id
 LEFT JOIN public.hr_employee_accounts a ON a.client_id=m.client_id AND a.user_id=u.id
 LEFT JOIN public.hr_employees e ON e.client_id=a.client_id AND e.id=a.employee_id
 WHERE m.client_id=p_client AND m.is_enabled AND u.is_enabled
 ORDER BY lower(u.display_name), u.id;
END $$;

CREATE OR REPLACE FUNCTION public.hr_owner_reporting_lines(p_client uuid) RETURNS TABLE(
    employee_id uuid, display_name text, manager_employee_id uuid, manager_user_id uuid)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF NOT public.hr_is_owner(p_client) THEN RETURN; END IF;
 RETURN QUERY
 SELECT e.id, e.display_name, e.manager_id, mu.id
 FROM public.hr_employees e
 LEFT JOIN public.hr_employee_accounts ma ON ma.client_id=e.client_id AND ma.employee_id=e.manager_id
 LEFT JOIN public.console_memberships mm ON mm.user_id=ma.user_id AND mm.client_id=ma.client_id AND mm.is_enabled
 LEFT JOIN public.console_users mu ON mu.id=mm.user_id AND mu.is_enabled
 WHERE e.client_id=p_client AND e.is_active
 ORDER BY lower(e.display_name), e.id;
END $$;

REVOKE ALL ON FUNCTION public.hr_owner_access_summary(uuid) FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_owner_access_summary(uuid) TO csa_app;
REVOKE ALL ON FUNCTION public.hr_owner_reporting_lines(uuid) FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_owner_reporting_lines(uuid) TO csa_app;
