-- HR processes, part 7: the template editor's preview, and a manual start that replaces only when asked.
-- Adds (csa_app still has no privilege on the 0630 tables; everything goes through these functions):
--   public.hr_template_preview(p_client,p_kind,p_rules): what a draft rule set means now, for the
--     template editor. {matching: the active people of p_client whose department (by id),
--     employment_type and region (exact) and legal_entity_reference (trimmed, case-insensitive) meet
--     every set rule (the rules of hr_process_match, 0631; a rule left null or blank matches anyone),
--     ties: [{template_id, name}] the active templates of the kind with as many set rules and rules
--     that can all hold at once (the conflicts a save reports, 0631; none for custom), oldest first,
--     total_active: the active people of p_client}. The HR owner and people.manage holders only:
--     NULL for anyone else and for another tenant than app.client_id, before p_rules is looked at.
--     p_kind and p_rules are validated as hr_template_apply validates them, with the same codes
--     (ERRCODE 23514): hr_template_kind_invalid, hr_template_rules_invalid,
--     hr_department_unavailable. Writes nothing. EXECUTE to csa_app only.
--   Internal helpers, no grant to any application role:
--     hr_template_rules_parse(p_client,p_rules): the rule validation of hr_template_write_rules
--       (0631), moved here unchanged so the preview and the write share it; returns the trimmed
--       rules (rule_department, rule_type, rule_entity, rule_region), an empty string as NULL.
--     hr_template_ties(p_client,p_kind,p_department,p_type,p_entity,p_region): the tie rule of
--       hr_template_conflicts (0631), moved here unchanged for a rule set that is not saved yet.
-- Changed (CREATE OR REPLACE, the rest of each body unchanged):
--   hr_template_write_rules (0631): validates through hr_template_rules_parse, then locks the rule's
--     department FOR SHARE as before.
--   hr_template_conflicts (0631): the other templates hr_template_ties returns for the template's
--     rules; same result, same order.
--   hr_process_start_apply (0632): a new body field replace. An onboarding or offboarding start for a
--     person who already has an active process of that kind is refused with
--     hr_process_already_active (nothing written) unless replace is JSON true; the other refusals
--     (kind, person, template, no matching template) come first. With replace true the old one is
--     cancelled (reason superseded, its open tasks skipped) as before. A custom process never
--     needs it (several may run). The person writes (the 0632 trigger: add, rehire, leave) never
--     ask: they keep cancelling and superseding by themselves (hr_process_start is unchanged).
-- No table, column or constraint change. Safe to run twice (CREATE OR REPLACE).
-- Pre-check: 0630-0635 applied.
-- Rollback (as the database owner, after the application no longer calls them): re-run the
--   hr_template_write_rules and hr_template_conflicts parts of 0631 and the hr_process_start_apply
--   part of 0632 (a manual start then silently supersedes again); then
--   DROP FUNCTION public.hr_template_preview(uuid,text,jsonb);
--   DROP FUNCTION public.hr_template_ties(uuid,text,uuid,text,text,text);
--   DROP FUNCTION public.hr_template_rules_parse(uuid,jsonb);
--   and delete the schema_migrations row for 0636.

-- 0631 hr_template_write_rules' validation, unchanged, without the write.
CREATE OR REPLACE FUNCTION public.hr_template_rules_parse(p_client uuid, p_rules jsonb,
    OUT rule_department uuid, OUT rule_type text, OUT rule_entity text, OUT rule_region text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_rules IS NULL OR jsonb_typeof(p_rules)='null' THEN p_rules:='{}'::jsonb; END IF;
 IF jsonb_typeof(p_rules) IS DISTINCT FROM 'object' OR EXISTS(
   SELECT 1 FROM jsonb_each(p_rules) f
   WHERE f.key NOT IN ('department_id','employment_type','legal_entity_reference','region')
    OR jsonb_typeof(f.value) NOT IN ('string','null')) THEN
  RAISE EXCEPTION 'hr_template_rules_invalid' USING ERRCODE='23514'; END IF;
 BEGIN
  rule_department:=nullif(btrim(p_rules->>'department_id'),'')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'hr_department_unavailable' USING ERRCODE='23514';
 END;
 IF rule_department IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM public.hr_departments d WHERE d.client_id=p_client AND d.id=rule_department) THEN
  RAISE EXCEPTION 'hr_department_unavailable' USING ERRCODE='23514'; END IF;
 rule_type:=nullif(btrim(p_rules->>'employment_type'),'');
 rule_entity:=nullif(btrim(p_rules->>'legal_entity_reference'),'');
 rule_region:=nullif(btrim(p_rules->>'region'),'');
 -- The lists of the hr_employees CHECKs (0520, 0410): a rule no person can meet would hide the
 -- template. tests/test_hr_process_templates.py pins them to the live constraints.
 IF rule_type NOT IN ('full_time','part_time','contract','intern')
  OR rule_region NOT IN ('peninsular_labuan','sabah','sarawak')
  OR char_length(rule_entity)>200 THEN
  RAISE EXCEPTION 'hr_template_rules_invalid' USING ERRCODE='23514'; END IF;
END $$;

-- 0631 hr_template_conflicts' tie rule, for any rule set: the active templates of the kind
-- (onboarding or offboarding; custom templates are picked by hand) with as many set rules, and no
-- rule set on both sides with different values. Stored rules are trimmed already.
-- The console keeps a copy of this rule: templateTies in web/lib/hr-template-form.ts (delete it
-- when TemplateSummary gains conflicts); change both together.
CREATE OR REPLACE FUNCTION public.hr_template_ties(p_client uuid, p_kind text, p_department uuid,
    p_type text, p_entity text, p_region text)
RETURNS TABLE(template_id uuid, name text, created_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT o.id, o.name, o.created_at
 FROM public.hr_process_templates o
 LEFT JOIN public.hr_process_template_rules b ON b.client_id=o.client_id AND b.template_id=o.id
 WHERE o.client_id=p_client AND o.kind=p_kind AND p_kind IN ('onboarding','offboarding') AND o.is_active
  AND num_nonnulls(p_department,p_type,p_entity,p_region)
    =num_nonnulls(b.department_id,b.employment_type,b.legal_entity_reference,b.region)
  AND (p_department IS NULL OR b.department_id IS NULL OR p_department=b.department_id)
  AND (p_type IS NULL OR b.employment_type IS NULL OR p_type=b.employment_type)
  AND (p_entity IS NULL OR b.legal_entity_reference IS NULL
       OR lower(p_entity)=lower(b.legal_entity_reference))
  AND (p_region IS NULL OR b.region IS NULL OR p_region=b.region)
$$;

-- 0631, validating through hr_template_rules_parse.
CREATE OR REPLACE FUNCTION public.hr_template_write_rules(p_client uuid, p_template uuid, p_rules jsonb)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r record;
BEGIN
 SELECT * INTO r FROM public.hr_template_rules_parse(p_client,p_rules);
 IF r.rule_department IS NOT NULL THEN
  PERFORM 1 FROM public.hr_departments d
   WHERE d.client_id=p_client AND d.id=r.rule_department FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'hr_department_unavailable' USING ERRCODE='23514'; END IF;
 END IF;
 DELETE FROM public.hr_process_template_rules x WHERE x.client_id=p_client AND x.template_id=p_template;
 IF num_nonnulls(r.rule_department,r.rule_type,r.rule_entity,r.rule_region)>0 THEN
  INSERT INTO public.hr_process_template_rules(client_id,template_id,department_id,employment_type,
   legal_entity_reference,region)
  VALUES(p_client,p_template,r.rule_department,r.rule_type,r.rule_entity,r.rule_region);
 END IF;
END $$;

-- 0631: the other active templates of the same kind that tie with this one for some person. Oldest
-- first. Empty for an inactive or custom template.
CREATE OR REPLACE FUNCTION public.hr_template_conflicts(p_client uuid, p_template uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT coalesce(jsonb_agg(o.template_id ORDER BY o.created_at, o.template_id),'[]'::jsonb)
 FROM public.hr_process_templates t
 LEFT JOIN public.hr_process_template_rules a ON a.client_id=t.client_id AND a.template_id=t.id
 CROSS JOIN LATERAL public.hr_template_ties(t.client_id,t.kind,a.department_id,a.employment_type,
  a.legal_entity_reference,a.region) o
 WHERE t.client_id=p_client AND t.id=p_template AND t.is_active AND o.template_id<>t.id
$$;

CREATE OR REPLACE FUNCTION public.hr_template_preview(p_client uuid, p_kind text, p_rules jsonb)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r record;
BEGIN
 -- Authority first, as hr_template_get: nothing, not even a validation code, for anyone else.
 IF public.hr_people_viewer(p_client) IS DISTINCT FROM 'hr' THEN RETURN NULL; END IF;
 IF p_kind IS NULL OR p_kind NOT IN ('onboarding','offboarding','custom') THEN
  RAISE EXCEPTION 'hr_template_kind_invalid' USING ERRCODE='23514'; END IF;
 SELECT * INTO r FROM public.hr_template_rules_parse(p_client,p_rules);
 -- matching: the person side of hr_process_match (0631).
 RETURN jsonb_build_object(
  'matching',(SELECT count(*)::integer FROM public.hr_employees e
   WHERE e.client_id=p_client AND e.is_active
    AND (r.rule_department IS NULL OR e.department_id=r.rule_department)
    AND (r.rule_type IS NULL OR e.employment_type=r.rule_type)
    AND (r.rule_entity IS NULL OR lower(btrim(e.legal_entity_reference))=lower(r.rule_entity))
    AND (r.rule_region IS NULL OR e.region=r.rule_region)),
  'ties',(SELECT coalesce(jsonb_agg(jsonb_build_object('template_id',x.template_id,'name',x.name)
     ORDER BY x.created_at, x.template_id),'[]'::jsonb)
   FROM public.hr_template_ties(p_client,p_kind,r.rule_department,r.rule_type,r.rule_entity,
    r.rule_region) x),
  'total_active',(SELECT count(*)::integer FROM public.hr_employees e
   WHERE e.client_id=p_client AND e.is_active));
END $$;

-- 0632, with the replace check: the active process is read before the start, raised after it.
CREATE OR REPLACE FUNCTION public.hr_process_start_apply(p_client uuid, p_key uuid, p_fingerprint text,
    p_body jsonb)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 old_op public.hr_operations%ROWTYPE; e public.hr_employees%ROWTYPE;
 pkind text:=p_body->>'kind'; target uuid; tmpl uuid; pid uuid; result jsonb; had_active boolean;
BEGIN
 -- Authority first: hr_process_match below does not check the tenant. Both checks are false for
 -- another tenant than app.client_id.
 IF public.hr_is_owner(p_client) IS NOT TRUE AND public.hr_can_manage_people(p_client) IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_people_manager_required' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-operations',0));
 SELECT * INTO old_op FROM public.hr_operations o
  WHERE o.client_id=p_client AND o.subject_id=actor AND o.request_key=p_key;
 IF FOUND THEN
  IF old_op.fingerprint IS DISTINCT FROM p_fingerprint THEN
   RAISE EXCEPTION 'hr_idempotency_conflict' USING ERRCODE='23514'; END IF;
  RETURN old_op.receipt||jsonb_build_object('duplicate',true);
 END IF;
 IF pkind IS NULL OR pkind NOT IN ('onboarding','offboarding','custom') THEN
  RAISE EXCEPTION 'hr_process_kind_invalid' USING ERRCODE='23514'; END IF;
 BEGIN
  target:=(p_body->>'employee_id')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514';
 END;
 BEGIN
  tmpl:=(p_body->>'template_id')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'hr_template_unavailable' USING ERRCODE='23514';
 END;
 -- NO KEY UPDATE still waits for the person writes (they take FOR UPDATE or update the row), but
 -- not for a concurrent new process, which takes only KEY SHARE for its foreign key: that one
 -- meets the one-active index (hr_process_already_active), never a silent supersede.
 SELECT * INTO e FROM public.hr_employees x WHERE x.client_id=p_client AND x.id=target FOR NO KEY UPDATE;
 IF target IS NULL OR NOT FOUND THEN RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514'; END IF;
 IF pkind<>'custom' AND e.is_active IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_person_not_active' USING ERRCODE='23514'; END IF;
 IF pkind='offboarding' AND e.last_day IS NULL THEN
  RAISE EXCEPTION 'hr_person_not_leaving' USING ERRCODE='23514'; END IF;
 -- 0636: HR replaces an active process of the kind only when it says so (the console asks first).
 -- Refused after hr_process_start, so a wrong template or no matching one is named first; the
 -- refusal rolls back everything the start did (the supersede included).
 had_active:=pkind<>'custom' AND EXISTS(
  SELECT 1 FROM public.hr_processes p
  WHERE p.client_id=p_client AND p.employee_id=target AND p.kind=pkind AND p.status='active');
 pid:=public.hr_process_start(p_client,target,pkind,tmpl,actor);
 IF pid IS NULL THEN RAISE EXCEPTION 'hr_process_no_template' USING ERRCODE='23514'; END IF;
 IF had_active AND p_body->'replace' IS DISTINCT FROM 'true'::jsonb THEN
  RAISE EXCEPTION 'hr_process_already_active' USING ERRCODE='23514'; END IF;
 SELECT jsonb_build_object('operation','process.start','process_id',p.id,'employee_id',p.employee_id,
  'kind',p.kind,'template_id',p.template_id,'status','saved','duplicate',false)
 INTO result FROM public.hr_processes p WHERE p.client_id=p_client AND p.id=pid;
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
 VALUES(p_client,actor,p_key,'process.start',p_fingerprint,result);
 RETURN result;
END $$;

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_template_rules_parse(uuid,jsonb)'::regprocedure,
  'public.hr_template_ties(uuid,text,uuid,text,text,text)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance',f);
 END LOOP;
 f:='public.hr_template_preview(uuid,text,jsonb)'::regprocedure;
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
 EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
END $$;
