-- People & HR project 1b: the Sensitive details switch, personal details, documents, leaving and the
-- assistant actions.
-- Adds (each task appends its own lines here, and its objects below):
--   The Sensitive details switch: the hr_grants scope/action CHECK hr_grants_scope_action (0520) also
--     lets the hr_operator scope hold people.sensitive (no fields).
--   public.hr_can_see_sensitive(p_client): true for the HR owner or a people.sensitive holder of the
--     tenant. EXECUTE to csa_app only.
-- No new roles and no FORCE ROW LEVEL SECURITY. Every function is SECURITY DEFINER and pinned to
-- search_path.
-- Pre-check: 0520 applied (hr_grants_scope_action, hr_can_manage_people).
-- Rollback (in this order; later tasks add their steps at the top, newest first):
--   DROP FUNCTION public.hr_can_see_sensitive(uuid);
--   DELETE FROM public.hr_grants WHERE action='people.sensitive';
--   ALTER TABLE public.hr_grants DROP CONSTRAINT hr_grants_scope_action, ADD CONSTRAINT
--     hr_grants_scope_action CHECK (the 0520 scope/action check: hr_operator with directory.read,
--     training.read and people.manage only).

-- ---- The Sensitive details switch ---------------------------------------------------------------

-- hr_grants: the 0520 scope/action CHECK, widened so the hr_operator scope may also hold
-- people.sensitive. The fields CHECK is left alone (it already allows fields on directory.read only).
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.hr_grants'::regclass
               AND conname='hr_grants_scope_action') THEN
  RAISE EXCEPTION 'hr_grants_scope_action (0520) is missing'; END IF;
 ALTER TABLE public.hr_grants DROP CONSTRAINT hr_grants_scope_action;
 ALTER TABLE public.hr_grants ADD CONSTRAINT hr_grants_scope_action CHECK (
  (scope='self' AND action IN ('directory.read','training.read','training.draft',
                               'training.submit','training.withdraw'))
  OR (scope='assigned_manager' AND action IN ('directory.read','training.read','training.decide'))
  OR (scope='hr_operator' AND action IN ('directory.read','training.read','people.manage',
                                         'people.sensitive'))
  OR (scope='client_owner' AND action='training.decide'));
END $$;

-- The HR owner, or a holder of the Sensitive details switch, of this tenant's session.
CREATE FUNCTION public.hr_can_see_sensitive(p_client uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN RETURN false; END IF;
 IF public.hr_is_owner(p_client) THEN RETURN true; END IF;
 PERFORM 1 FROM public.hr_grants WHERE client_id=p_client
  AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid
  AND action='people.sensitive' AND scope='hr_operator' FOR SHARE;
 RETURN FOUND;
END $$;

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_can_see_sensitive(uuid)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;
