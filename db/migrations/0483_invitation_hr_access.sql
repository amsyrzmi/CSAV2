-- Invitation HR access (B4): an invitation records which HR access preset the inviting admin chose,
-- and who chose it, so acceptance can apply it for the new member.
-- Adds two nullable columns to public.console_tokens:
--   hr_preset text: one of staff, manager, hr_team, payroll_preparer, payroll_reviewer (the B2 preset ids).
--   hr_preset_chosen_by uuid: the console user who chose it (an admin, the tenant's HR owner for any
--     preset other than staff).
-- Only invitations may carry a preset, and the preset and its chooser are set together or not at all.
-- Adds five HR functions, all SECURITY DEFINER, pinned to search_path, EXECUTE to csa_app only:
--   public.hr_invitation_preset(p_client, p_token): the consumed invitation's preset, chooser, whether
--     the chooser is the HR owner now, whether it was applied or dismissed already, and the member's
--     current grant, workflow and area rows (the hr_owner_access_summary JSON shapes).
--   public.hr_apply_invitation_access(p_client, p_token, p_rows, p_on, p_added, p_owner_override):
--     adds the preset's rows to the invitee's access (never removes) and records operation
--     invitation_access, keyed by the token, with the resulting switches (p_on) and the ones it turned
--     on (p_added). A chooser who is not the HR owner is held to the Staff pairs, unless the current
--     HR owner applies it (p_owner_override). p_on and p_added are recorded, never trusted.
--   public.hr_dismiss_invitation_access(p_client, p_token): owner only, records the dismissal.
--   public.hr_owner_pending_invitation_access(p_client): owner only, the accepted invitations whose
--     access was neither applied nor dismissed.
--   public.hr_owner_actor_names(p_client, p_ids): owner only, names for Recent changes, including
--     platform admins and disabled members.
-- No new tables, roles, policies or grants to csa_identity: it already has SELECT, INSERT and UPDATE
-- on console_tokens, and the identity readiness checks are unchanged.
-- Pre-check: 0270 applied (console_tokens), 0410 (hr_operations, hr_authority_events, hr_is_owner).
--   Safe to run twice: columns use IF NOT EXISTS, the constraints are added only when missing, the
--   functions use CREATE OR REPLACE, and REVOKE/GRANT are idempotent.
-- Rollback: DROP FUNCTION public.hr_owner_actor_names(uuid,uuid[]);
--   DROP FUNCTION public.hr_owner_pending_invitation_access(uuid);
--   DROP FUNCTION public.hr_dismiss_invitation_access(uuid,uuid);
--   DROP FUNCTION public.hr_apply_invitation_access(uuid,uuid,jsonb,text[],text[],boolean);
--   DROP FUNCTION public.hr_invitation_preset(uuid,uuid);
--   ALTER TABLE public.console_tokens
--   DROP CONSTRAINT console_tokens_hr_preset_pair,
--   DROP CONSTRAINT console_tokens_hr_preset_invite_only,
--   DROP CONSTRAINT console_tokens_hr_preset_known,
--   DROP COLUMN hr_preset_chosen_by, DROP COLUMN hr_preset;

ALTER TABLE public.console_tokens
    ADD COLUMN IF NOT EXISTS hr_preset text,
    ADD COLUMN IF NOT EXISTS hr_preset_chosen_by uuid REFERENCES public.console_users(id);

DO $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='console_tokens_hr_preset_known'
                AND conrelid='public.console_tokens'::regclass) THEN
  ALTER TABLE public.console_tokens ADD CONSTRAINT console_tokens_hr_preset_known
   CHECK (hr_preset IN ('staff','manager','hr_team','payroll_preparer','payroll_reviewer'));
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='console_tokens_hr_preset_invite_only'
                AND conrelid='public.console_tokens'::regclass) THEN
  ALTER TABLE public.console_tokens ADD CONSTRAINT console_tokens_hr_preset_invite_only
   CHECK (kind = 'invite' OR (hr_preset IS NULL AND hr_preset_chosen_by IS NULL));
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='console_tokens_hr_preset_pair'
                AND conrelid='public.console_tokens'::regclass) THEN
  ALTER TABLE public.console_tokens ADD CONSTRAINT console_tokens_hr_preset_pair
   CHECK ((hr_preset IS NULL) = (hr_preset_chosen_by IS NULL));
 END IF;
END $$;

-- The invitation's preset, for an accepted invite of this tenant whose user is an enabled member.
CREATE OR REPLACE FUNCTION public.hr_invitation_preset(p_client uuid, p_token uuid) RETURNS TABLE(
    user_id uuid, preset text, chosen_by uuid, chooser_is_owner boolean, applied boolean,
    grants jsonb, workflows jsonb, areas jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS NULL OR p_token IS NULL
  OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN RETURN; END IF;
 RETURN QUERY
 SELECT t.user_id, t.hr_preset, t.hr_preset_chosen_by,
  -- A platform admin never carries a tenant HR owner's right to grant presets.
  EXISTS(SELECT 1 FROM public.hr_client_owners o JOIN public.console_users c ON c.id=o.user_id
         WHERE o.client_id=p_client AND o.user_id=t.hr_preset_chosen_by AND NOT c.is_platform_admin),
  EXISTS(SELECT 1 FROM public.hr_operations op
         WHERE op.client_id=p_client AND op.request_key=p_token
         AND op.operation IN ('invitation_access','invitation_access.dismiss')),
  COALESCE((SELECT jsonb_agg(jsonb_build_object('action',g.action,'scope',g.scope,'fields',to_jsonb(g.fields))
            ORDER BY g.action,g.scope)
            FROM public.hr_grants g WHERE g.client_id=p_client AND g.user_id=t.user_id),'[]'::jsonb),
  COALESCE((SELECT jsonb_agg(jsonb_build_object('domain',w.domain,'action',w.action,'scope',w.scope)
            ORDER BY w.domain,w.action,w.scope)
            FROM public.hr_workflow_grants w WHERE w.client_id=p_client AND w.user_id=t.user_id),'[]'::jsonb),
  COALESCE((SELECT jsonb_agg(pg.area ORDER BY pg.area)
            FROM public.hr_private_grants pg WHERE pg.client_id=p_client AND pg.user_id=t.user_id),'[]'::jsonb)
 FROM public.console_tokens t
 JOIN public.console_users u ON u.id=t.user_id
 JOIN public.console_memberships m ON m.user_id=t.user_id AND m.client_id=t.client_id
 WHERE t.id=p_token AND t.kind='invite' AND t.client_id=p_client
  AND t.consumed_at IS NOT NULL AND t.revoked_at IS NULL AND t.hr_preset IS NOT NULL
  AND m.is_enabled AND u.is_enabled;
END $$;

CREATE OR REPLACE FUNCTION public.hr_apply_invitation_access(
    p_client uuid, p_token uuid, p_rows jsonb, p_on text[], p_added text[], p_owner_override boolean)
    RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 -- The Staff preset's (action, scope) grant pairs and (domain, action, scope) workflow rows: the only
 -- SQL copy of rows(PRESETS["staff"], EMPTY) in csa/hr/access_switches.py. Staff has no areas.
 -- tests/test_hr_invitation_access.py compares the two.
 staff constant jsonb := '{"grants":[["directory.read","hr_operator"],["directory.read","self"],["training.draft","self"],["training.read","self"],["training.submit","self"],["training.withdraw","self"]],"workflows":[["attendance","prepare","self"],["attendance","read","self"],["leave","prepare","self"],["leave","read","self"]]}';
 actor uuid := nullif(current_setting('app.hr_user_id',true),'')::uuid;
 tok record; owner_chooser boolean; approver uuid; item jsonb; result jsonb;
BEGIN
 -- Every guard is written so a NULL argument can never skip it (three-valued logic).
 IF p_client IS NULL OR p_token IS NULL
  OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN
  RAISE EXCEPTION 'hr_invitation_unavailable' USING ERRCODE='23514'; END IF;
 IF p_owner_override IS NULL OR jsonb_typeof(p_rows) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_rows->'grants') IS DISTINCT FROM 'array'
  OR jsonb_typeof(p_rows->'workflows') IS DISTINCT FROM 'array'
  OR jsonb_typeof(p_rows->'areas') IS DISTINCT FROM 'array'
  OR p_on IS NULL OR p_added IS NULL OR cardinality(p_on)>8 OR cardinality(p_added)>8 THEN
  RAISE EXCEPTION 'hr_invitation_arguments_invalid' USING ERRCODE='23514'; END IF;
 IF p_owner_override IS TRUE AND public.hr_is_owner(p_client) IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_owner_required' USING ERRCODE='42501'; END IF;
 -- The token row lock serialises apply and dismiss for one invitation.
 SELECT t.user_id, t.hr_preset, t.hr_preset_chosen_by INTO tok FROM public.console_tokens t
  WHERE t.id=p_token AND t.kind='invite' AND t.client_id=p_client
  AND t.consumed_at IS NOT NULL AND t.revoked_at IS NULL AND t.hr_preset IS NOT NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'hr_invitation_unavailable' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM public.console_memberships m JOIN public.console_users u ON u.id=m.user_id
  WHERE m.client_id=p_client AND m.user_id=tok.user_id AND m.is_enabled AND u.is_enabled FOR SHARE OF m;
 IF NOT FOUND THEN RAISE EXCEPTION 'hr_invitation_unavailable' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.hr_operations op WHERE op.client_id=p_client AND op.request_key=p_token
           AND op.operation IN ('invitation_access','invitation_access.dismiss')) THEN
  RAISE EXCEPTION 'hr_invitation_applied' USING ERRCODE='23514'; END IF;
 owner_chooser := EXISTS(SELECT 1 FROM public.hr_client_owners o JOIN public.console_users c ON c.id=o.user_id
  WHERE o.client_id=p_client AND o.user_id=tok.hr_preset_chosen_by AND NOT c.is_platform_admin FOR SHARE OF o);
 IF owner_chooser IS NOT TRUE AND p_owner_override IS NOT TRUE THEN
  IF tok.hr_preset IS DISTINCT FROM 'staff' THEN
   RAISE EXCEPTION 'hr_invitation_owner_required' USING ERRCODE='42501'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_rows->'grants') g
            WHERE NOT staff->'grants' @> jsonb_build_array(jsonb_build_array(g->>'action',g->>'scope')))
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_rows->'workflows') w
            WHERE NOT staff->'workflows' @> jsonb_build_array(
             jsonb_build_array(w->>'domain',w->>'action',w->>'scope')))
   OR jsonb_array_length(p_rows->'areas')>0 THEN
   RAISE EXCEPTION 'hr_invitation_rows_refused' USING ERRCODE='42501'; END IF;
 END IF;
 -- Add, never remove: the result is the union of the member's rows and p_rows, so access the HR
 -- owner already gave is kept. A grant for the same (action, scope) keeps the union of its fields.
 FOR item IN SELECT * FROM jsonb_array_elements(p_rows->'grants') LOOP
  INSERT INTO public.hr_grants AS h(client_id,user_id,action,scope,fields)
  VALUES(p_client,tok.user_id,item->>'action',item->>'scope',
         ARRAY(SELECT jsonb_array_elements_text(COALESCE(item->'fields','[]'::jsonb))))
  ON CONFLICT (client_id,user_id,action,scope) DO UPDATE
  SET fields=ARRAY(SELECT DISTINCT f FROM unnest(h.fields||EXCLUDED.fields) AS f ORDER BY f);
 END LOOP;
 FOR item IN SELECT * FROM jsonb_array_elements(p_rows->'workflows') LOOP
  INSERT INTO public.hr_workflow_grants(client_id,user_id,domain,action,scope)
  VALUES(p_client,tok.user_id,item->>'domain',item->>'action',item->>'scope') ON CONFLICT DO NOTHING;
 END LOOP;
 -- Areas reach this point only for an owner chooser or an owner override, both members.
 approver := CASE WHEN p_owner_override THEN actor ELSE tok.hr_preset_chosen_by END;
 FOR item IN SELECT * FROM jsonb_array_elements(p_rows->'areas') LOOP
  INSERT INTO public.hr_private_grants(client_id,user_id,area,approved_by)
  VALUES(p_client,tok.user_id,item#>>'{}',approver) ON CONFLICT DO NOTHING;
 END LOOP;
 result := jsonb_build_object('operation','invitation_access','resource_id',tok.user_id,'version',0,
  'status','saved','duplicate',false,'preset',tok.hr_preset,
  'applied_by',CASE WHEN p_owner_override THEN actor END);
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
 VALUES(p_client,tok.user_id,p_token,'invitation_access',
        encode(sha256(convert_to(p_rows::text,'UTF8')),'hex'),result);
 INSERT INTO public.hr_authority_events(client_id,subject_id,request_key,operation,details)
 VALUES(p_client,tok.user_id,p_token,'invitation_access',jsonb_build_object(
  'user_id',tok.user_id,'preset',tok.hr_preset,'on',to_jsonb(p_on),'added',to_jsonb(p_added),
  'chosen_by',tok.hr_preset_chosen_by));
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION public.hr_dismiss_invitation_access(p_client uuid, p_token uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE actor uuid := nullif(current_setting('app.hr_user_id',true),'')::uuid; tok record;
BEGIN
 IF public.hr_is_owner(p_client) IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_owner_required' USING ERRCODE='42501'; END IF;
 SELECT t.user_id, t.hr_preset, t.hr_preset_chosen_by INTO tok FROM public.console_tokens t
  WHERE t.id=p_token AND t.kind='invite' AND t.client_id=p_client
  AND t.consumed_at IS NOT NULL AND t.revoked_at IS NULL AND t.hr_preset IS NOT NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'hr_invitation_unavailable' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.hr_operations op WHERE op.client_id=p_client AND op.request_key=p_token
           AND op.operation IN ('invitation_access','invitation_access.dismiss')) THEN
  RAISE EXCEPTION 'hr_invitation_applied' USING ERRCODE='23514'; END IF;
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
 VALUES(p_client,actor,p_token,'invitation_access.dismiss',
        encode(sha256(convert_to('invitation_access.dismiss:'||p_token::text,'UTF8')),'hex'),
        jsonb_build_object('operation','invitation_access.dismiss','resource_id',tok.user_id,
                           'version',0,'status','dismissed','duplicate',false));
 INSERT INTO public.hr_authority_events(client_id,subject_id,request_key,operation,details)
 VALUES(p_client,actor,p_token,'invitation_access.dismiss',jsonb_build_object(
  'user_id',tok.user_id,'preset',tok.hr_preset,'chosen_by',tok.hr_preset_chosen_by));
END $$;

CREATE OR REPLACE FUNCTION public.hr_owner_pending_invitation_access(p_client uuid) RETURNS TABLE(
    token_id uuid, user_id uuid, display_name text, preset text, chosen_by uuid, chooser_name text,
    accepted_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF public.hr_is_owner(p_client) IS NOT TRUE THEN RETURN; END IF;
 RETURN QUERY
 SELECT t.id, t.user_id, u.display_name, t.hr_preset, t.hr_preset_chosen_by,
  CASE WHEN c.is_platform_admin THEN 'FourStacks admin'
       WHEN c.is_enabled AND cm.is_enabled THEN c.display_name END,
  t.consumed_at
 FROM public.console_tokens t
 JOIN public.console_users u ON u.id=t.user_id
 JOIN public.console_memberships m ON m.user_id=t.user_id AND m.client_id=t.client_id
 JOIN public.console_users c ON c.id=t.hr_preset_chosen_by
 LEFT JOIN public.console_memberships cm ON cm.user_id=c.id AND cm.client_id=p_client
 WHERE t.client_id=p_client AND t.kind='invite' AND t.consumed_at IS NOT NULL
  AND t.revoked_at IS NULL AND t.hr_preset IS NOT NULL AND m.is_enabled AND u.is_enabled
  AND NOT EXISTS(SELECT 1 FROM public.hr_operations op
                 WHERE op.client_id=p_client AND op.request_key=t.id
                 AND op.operation IN ('invitation_access','invitation_access.dismiss'))
 ORDER BY t.consumed_at DESC, t.id;
END $$;

-- Names for Recent changes: this tenant's members, enabled or not, and platform admins.
CREATE OR REPLACE FUNCTION public.hr_owner_actor_names(p_client uuid, p_ids uuid[]) RETURNS TABLE(
    user_id uuid, display_name text, is_platform_admin boolean, is_enabled_member boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF public.hr_is_owner(p_client) IS NOT TRUE THEN RETURN; END IF;
 RETURN QUERY
 SELECT u.id, u.display_name, u.is_platform_admin, COALESCE(m.is_enabled AND u.is_enabled, false)
 FROM public.console_users u
 LEFT JOIN public.console_memberships m ON m.user_id=u.id AND m.client_id=p_client
 WHERE u.id=ANY(p_ids) AND (m.user_id IS NOT NULL OR u.is_platform_admin);
END $$;

REVOKE ALL ON FUNCTION public.hr_invitation_preset(uuid,uuid) FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_invitation_preset(uuid,uuid) TO csa_app;
REVOKE ALL ON FUNCTION public.hr_apply_invitation_access(uuid,uuid,jsonb,text[],text[],boolean)
 FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_apply_invitation_access(uuid,uuid,jsonb,text[],text[],boolean) TO csa_app;
REVOKE ALL ON FUNCTION public.hr_dismiss_invitation_access(uuid,uuid) FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_dismiss_invitation_access(uuid,uuid) TO csa_app;
REVOKE ALL ON FUNCTION public.hr_owner_pending_invitation_access(uuid) FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_owner_pending_invitation_access(uuid) TO csa_app;
REVOKE ALL ON FUNCTION public.hr_owner_actor_names(uuid,uuid[]) FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_owner_actor_names(uuid,uuid[]) TO csa_app;
