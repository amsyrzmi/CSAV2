-- HR processes, part 2: writing and reading process templates, and picking the template for a person.
-- Adds (csa_app still has no privilege on the 0630 tables; everything goes through these functions):
--   public.hr_template_apply(p_client,p_key,p_fingerprint,p_body): the template write path, for the
--     HR owner or a people.manage holder (hr_people_manager_required otherwise, also for another
--     tenant). Operations: template.create {kind,name,rules,steps}; template.update {template_id,
--     expected_version,name,rules,steps,is_active} (version + 1, rules and steps replaced, the kind
--     never changes); template.deactivate {template_id,expected_version} (version + 1). Takes the
--     tenant's hr-operations lock, returns the stored receipt for a repeated (key, fingerprint) and
--     refuses the same key with another fingerprint (hr_idempotency_conflict), as hr_personal_apply
--     (0521). Every change writes one receipt in hr_operations and one console_audit row
--     (hr.template_created, hr.template_updated, hr.template_deactivated). Result: {operation,
--     template_id, version, conflicts: [template ids], warnings: [{code, ordinal}], status,
--     duplicate}. A template id of another tenant is hr_template_unavailable; a stale
--     expected_version is hr_template_changed. EXECUTE to csa_app only.
--   Validation (ERRCODE 23514, nothing written): hr_template_kind_invalid, hr_template_name_invalid
--     (1-120 characters after trimming), hr_template_invalid (is_active not a boolean),
--     hr_template_rules_invalid (only department_id, employment_type, legal_entity_reference and
--     region, each a string or null; employment_type and region from the hr_employees lists;
--     legal_entity_reference at most 200 characters), hr_department_unavailable (a rule department
--     not of this tenant), hr_template_steps_invalid (1-40 steps, each an object of known keys,
--     text keys strings, required a boolean), hr_template_step_title_invalid (English 1-160,
--     Malay at most 160), hr_template_step_text_invalid (descriptions at most 2000, reference_note
--     at most 500), hr_template_step_owner_invalid, hr_template_step_person_unavailable (a person
--     step needs an active employee of this tenant), hr_template_step_anchor_invalid (onboarding:
--     start_date or probation_end; offboarding: last_day; custom: any),
--     hr_template_step_offset_invalid (an integer within -365..365), hr_template_step_approval_invalid,
--     hr_template_step_check_invalid (document_uploaded:<category> names a kind of
--     hr_employee_documents: contract, identity, certificate or other). Rule and step text is
--     trimmed and an empty string is stored as NULL; steps are numbered by their array position.
--   Warnings, never errors: hr_template_step_after_last_day for a person or employee step of an
--     offboarding template due after last_day (offset > 0). Conflicts: the other active templates of
--     the same kind (onboarding or offboarding; custom templates are picked by hand) with as many set
--     rules and rules that can all hold at once, so one person could match both.
--   public.hr_template_rows(p_client), hr_template_get(p_client,p_template): the templates (rules
--     and step count) and one template (rules, steps, conflicts, warnings) to the HR owner and
--     people.manage holders; nothing (no rows, NULL) to anyone else or for another tenant's id.
--     EXECUTE to csa_app only.
--   public.hr_process_match(p_client,p_employee,p_kind): the template a person's process of that kind
--     uses, NULL when none. Among the active templates of the kind whose set rules all match
--     (department by id, employment_type and region exact, legal_entity_reference trimmed and
--     case-insensitive), the most set rules win, then the oldest created_at; a template with no rule
--     is the default. Internal (the 0632 trigger calls it): no grant to any application role.
--   Internal helpers, no grant to any application role: hr_template_write_rules,
--     hr_template_write_steps, hr_template_conflicts, hr_template_warnings.
-- No table, column or constraint change. Safe to run twice (CREATE OR REPLACE).
-- Pre-check: 0630 (process tables) and 0521 (hr_employee_documents, hr_operations) applied.
-- Rollback (as the database owner, after the application no longer calls them):
--   DROP FUNCTION public.hr_process_match(uuid,uuid,text); DROP FUNCTION public.hr_template_get(uuid,uuid);
--   DROP FUNCTION public.hr_template_rows(uuid); DROP FUNCTION public.hr_template_apply(uuid,uuid,text,jsonb);
--   DROP FUNCTION public.hr_template_warnings(uuid,uuid); DROP FUNCTION public.hr_template_conflicts(uuid,uuid);
--   DROP FUNCTION public.hr_template_write_steps(uuid,uuid,text,jsonb);
--   DROP FUNCTION public.hr_template_write_rules(uuid,uuid,jsonb);
--   then delete the schema_migrations row for 0631. Templates already written stay (0630 tables).

-- Replaces the rules of a template: one row when any rule is set, none for the company default.
CREATE OR REPLACE FUNCTION public.hr_template_write_rules(p_client uuid, p_template uuid, p_rules jsonb)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE dept uuid; etype text; entity text; reg text;
BEGIN
 IF p_rules IS NULL OR jsonb_typeof(p_rules)='null' THEN p_rules:='{}'::jsonb; END IF;
 IF jsonb_typeof(p_rules) IS DISTINCT FROM 'object' OR EXISTS(
   SELECT 1 FROM jsonb_each(p_rules) f
   WHERE f.key NOT IN ('department_id','employment_type','legal_entity_reference','region')
    OR jsonb_typeof(f.value) NOT IN ('string','null')) THEN
  RAISE EXCEPTION 'hr_template_rules_invalid' USING ERRCODE='23514'; END IF;
 BEGIN
  dept:=nullif(btrim(p_rules->>'department_id'),'')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'hr_department_unavailable' USING ERRCODE='23514';
 END;
 IF dept IS NOT NULL THEN
  PERFORM 1 FROM public.hr_departments d WHERE d.client_id=p_client AND d.id=dept FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'hr_department_unavailable' USING ERRCODE='23514'; END IF;
 END IF;
 etype:=nullif(btrim(p_rules->>'employment_type'),'');
 entity:=nullif(btrim(p_rules->>'legal_entity_reference'),'');
 reg:=nullif(btrim(p_rules->>'region'),'');
 -- The lists of the hr_employees CHECKs (0520, 0410): a rule no person can meet would hide the
 -- template. tests/test_hr_process_templates.py pins them to the live constraints.
 IF etype NOT IN ('full_time','part_time','contract','intern')
  OR reg NOT IN ('peninsular_labuan','sabah','sarawak')
  OR char_length(entity)>200 THEN
  RAISE EXCEPTION 'hr_template_rules_invalid' USING ERRCODE='23514'; END IF;
 DELETE FROM public.hr_process_template_rules r WHERE r.client_id=p_client AND r.template_id=p_template;
 IF num_nonnulls(dept,etype,entity,reg)>0 THEN
  INSERT INTO public.hr_process_template_rules(client_id,template_id,department_id,employment_type,
   legal_entity_reference,region)
  VALUES(p_client,p_template,dept,etype,entity,reg);
 END IF;
END $$;

-- Replaces the steps of a template of kind p_kind; ordinals follow the array order.
CREATE OR REPLACE FUNCTION public.hr_template_write_steps(p_client uuid, p_template uuid, p_kind text,
    p_steps jsonb)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 item record; s jsonb; title text; title_ms text; desc_en text; desc_ms text; note text;
 role text; person uuid; anchor text; offset_days numeric; needed boolean; approval text; check_name text;
BEGIN
 IF jsonb_typeof(p_steps) IS DISTINCT FROM 'array' OR jsonb_array_length(p_steps) NOT BETWEEN 1 AND 40 THEN
  RAISE EXCEPTION 'hr_template_steps_invalid' USING ERRCODE='23514'; END IF;
 DELETE FROM public.hr_process_template_steps x WHERE x.client_id=p_client AND x.template_id=p_template;
 FOR item IN SELECT e.value AS step, e.ordinality::integer AS n
             FROM jsonb_array_elements(p_steps) WITH ORDINALITY e LOOP
  s:=item.step;
  IF jsonb_typeof(s) IS DISTINCT FROM 'object' OR EXISTS(
    SELECT 1 FROM jsonb_each(s) f
    WHERE f.key NOT IN ('title_en','title_ms','description_en','description_ms','owner_role',
                        'owner_employee_id','anchor','due_offset_days','required','approval_by',
                        'auto_check','reference_note')
     OR (f.key NOT IN ('due_offset_days','required') AND jsonb_typeof(f.value) NOT IN ('string','null'))
     OR (f.key='required' AND jsonb_typeof(f.value) NOT IN ('boolean','null'))) THEN
   RAISE EXCEPTION 'hr_template_steps_invalid' USING ERRCODE='23514'; END IF;
  title:=nullif(btrim(s->>'title_en'),'');
  title_ms:=nullif(btrim(s->>'title_ms'),'');
  IF title IS NULL OR char_length(title)>160 OR char_length(title_ms)>160 THEN
   RAISE EXCEPTION 'hr_template_step_title_invalid' USING ERRCODE='23514'; END IF;
  desc_en:=nullif(btrim(s->>'description_en'),'');
  desc_ms:=nullif(btrim(s->>'description_ms'),'');
  note:=nullif(btrim(s->>'reference_note'),'');
  IF char_length(desc_en)>2000 OR char_length(desc_ms)>2000 OR char_length(note)>500 THEN
   RAISE EXCEPTION 'hr_template_step_text_invalid' USING ERRCODE='23514'; END IF;
  role:=s->>'owner_role';
  IF role IS NULL OR role NOT IN ('hr','manager','employee','person')
   OR (role<>'person' AND s->>'owner_employee_id' IS NOT NULL) THEN
   RAISE EXCEPTION 'hr_template_step_owner_invalid' USING ERRCODE='23514'; END IF;
  person:=NULL;
  IF role='person' THEN
   BEGIN
    person:=(s->>'owner_employee_id')::uuid;
   EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'hr_template_step_person_unavailable' USING ERRCODE='23514';
   END;
   PERFORM 1 FROM public.hr_employees e
    WHERE e.client_id=p_client AND e.id=person AND e.is_active FOR SHARE;
   IF person IS NULL OR NOT FOUND THEN
    RAISE EXCEPTION 'hr_template_step_person_unavailable' USING ERRCODE='23514'; END IF;
  END IF;
  anchor:=s->>'anchor';
  IF anchor IS NULL OR anchor NOT IN ('start_date','last_day','probation_end')
   OR (p_kind='onboarding' AND anchor NOT IN ('start_date','probation_end'))
   OR (p_kind='offboarding' AND anchor<>'last_day') THEN
   RAISE EXCEPTION 'hr_template_step_anchor_invalid' USING ERRCODE='23514'; END IF;
  IF jsonb_typeof(s->'due_offset_days') IS DISTINCT FROM 'number' THEN
   RAISE EXCEPTION 'hr_template_step_offset_invalid' USING ERRCODE='23514'; END IF;
  offset_days:=(s->>'due_offset_days')::numeric;
  IF offset_days<>trunc(offset_days) OR offset_days NOT BETWEEN -365 AND 365 THEN
   RAISE EXCEPTION 'hr_template_step_offset_invalid' USING ERRCODE='23514'; END IF;
  needed:=coalesce((s->>'required')::boolean,true);
  approval:=s->>'approval_by';
  IF approval NOT IN ('hr','manager') THEN
   RAISE EXCEPTION 'hr_template_step_approval_invalid' USING ERRCODE='23514'; END IF;
  check_name:=s->>'auto_check';
  -- The document categories are the kinds of hr_employee_documents (0521);
  -- tests/test_hr_process_templates.py pins them to the live constraint.
  IF check_name NOT IN ('account_linked','personal_details_complete','access_removed',
                        'document_uploaded:contract','document_uploaded:identity',
                        'document_uploaded:certificate','document_uploaded:other') THEN
   RAISE EXCEPTION 'hr_template_step_check_invalid' USING ERRCODE='23514'; END IF;
  INSERT INTO public.hr_process_template_steps(client_id,template_id,ordinal,title_en,title_ms,
   description_en,description_ms,owner_role,owner_employee_id,anchor,due_offset_days,required,
   approval_by,auto_check,reference_note)
  VALUES(p_client,p_template,item.n,title,title_ms,desc_en,desc_ms,role,person,anchor,
   offset_days::integer,needed,approval,check_name,note);
 END LOOP;
END $$;

-- The other active templates of the same kind that tie with this one for some person: as many set
-- rules, and no rule set on both sides with different values. Oldest first. Empty for an inactive
-- or custom template.
CREATE OR REPLACE FUNCTION public.hr_template_conflicts(p_client uuid, p_template uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT coalesce(jsonb_agg(o.id ORDER BY o.created_at, o.id),'[]'::jsonb)
 FROM public.hr_process_templates t
 LEFT JOIN public.hr_process_template_rules a ON a.client_id=t.client_id AND a.template_id=t.id
 JOIN public.hr_process_templates o ON o.client_id=t.client_id AND o.kind=t.kind AND o.id<>t.id
  AND o.is_active
 LEFT JOIN public.hr_process_template_rules b ON b.client_id=o.client_id AND b.template_id=o.id
 WHERE t.client_id=p_client AND t.id=p_template AND t.is_active AND t.kind IN ('onboarding','offboarding')
  AND num_nonnulls(a.department_id,a.employment_type,a.legal_entity_reference,a.region)
    =num_nonnulls(b.department_id,b.employment_type,b.legal_entity_reference,b.region)
  AND (a.department_id IS NULL OR b.department_id IS NULL OR a.department_id=b.department_id)
  AND (a.employment_type IS NULL OR b.employment_type IS NULL OR a.employment_type=b.employment_type)
  AND (a.legal_entity_reference IS NULL OR b.legal_entity_reference IS NULL
       OR lower(a.legal_entity_reference)=lower(b.legal_entity_reference))
  AND (a.region IS NULL OR b.region IS NULL OR a.region=b.region)
$$;

-- Spec 5.7.7: a leaver-owned step due after last_day is clamped when the process starts; the
-- author is told when saving.
CREATE OR REPLACE FUNCTION public.hr_template_warnings(p_client uuid, p_template uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT coalesce(jsonb_agg(jsonb_build_object('code','hr_template_step_after_last_day','ordinal',s.ordinal)
                           ORDER BY s.ordinal),'[]'::jsonb)
 FROM public.hr_process_templates t
 JOIN public.hr_process_template_steps s ON s.client_id=t.client_id AND s.template_id=t.id
 WHERE t.client_id=p_client AND t.id=p_template AND t.kind='offboarding'
  AND s.owner_role IN ('person','employee') AND s.due_offset_days>0
$$;

CREATE OR REPLACE FUNCTION public.hr_template_apply(p_client uuid, p_key uuid, p_fingerprint text,
    p_body jsonb)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 op text:=p_body->>'operation';
 old_op public.hr_operations%ROWTYPE; cur public.hr_process_templates%ROWTYPE;
 target uuid; tkind text; tname text; active boolean; v integer; verb text; result jsonb;
BEGIN
 -- Written so a NULL can never skip a guard (three-valued logic). Both checks are false for
 -- another tenant than app.client_id.
 IF public.hr_is_owner(p_client) IS NOT TRUE AND public.hr_can_manage_people(p_client) IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_people_manager_required' USING ERRCODE='42501'; END IF;
 -- One bounded HR operation at a time per tenant, the lock HrOperations.apply takes first.
 PERFORM pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-operations',0));
 -- A repeated request returns the first receipt before anything else is looked at, so a retry
 -- after a lost response never meets the version check it already passed.
 SELECT * INTO old_op FROM public.hr_operations o
  WHERE o.client_id=p_client AND o.subject_id=actor AND o.request_key=p_key;
 IF FOUND THEN
  IF old_op.fingerprint IS DISTINCT FROM p_fingerprint THEN
   RAISE EXCEPTION 'hr_idempotency_conflict' USING ERRCODE='23514'; END IF;
  RETURN old_op.receipt||jsonb_build_object('duplicate',true);
 END IF;
 IF op='template.create' THEN
  tkind:=p_body->>'kind';
  IF tkind IS NULL OR tkind NOT IN ('onboarding','offboarding','custom') THEN
   RAISE EXCEPTION 'hr_template_kind_invalid' USING ERRCODE='23514'; END IF;
 ELSIF op IN ('template.update','template.deactivate') THEN
  BEGIN
   target:=(p_body->>'template_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
   RAISE EXCEPTION 'hr_template_unavailable' USING ERRCODE='23514';
  END;
  SELECT * INTO cur FROM public.hr_process_templates t
   WHERE t.client_id=p_client AND t.id=target FOR UPDATE;
  IF target IS NULL OR NOT FOUND THEN
   RAISE EXCEPTION 'hr_template_unavailable' USING ERRCODE='23514'; END IF;
  IF jsonb_typeof(p_body->'expected_version') IS DISTINCT FROM 'number'
   OR cur.version::numeric<>(p_body->>'expected_version')::numeric THEN
   RAISE EXCEPTION 'hr_template_changed' USING ERRCODE='23514'; END IF;
  tkind:=cur.kind;
 ELSE
  RAISE EXCEPTION 'hr_operation_unsupported' USING ERRCODE='23514';
 END IF;
 IF op IN ('template.create','template.update') THEN
  tname:=CASE WHEN jsonb_typeof(p_body->'name')='string' THEN btrim(p_body->>'name') END;
  IF tname IS NULL OR char_length(tname) NOT BETWEEN 1 AND 120 THEN
   RAISE EXCEPTION 'hr_template_name_invalid' USING ERRCODE='23514'; END IF;
 END IF;
 IF op='template.create' THEN
  INSERT INTO public.hr_process_templates(client_id,kind,name)
  VALUES(p_client,tkind,tname) RETURNING id, version INTO target, v;
  verb:='created';
 ELSIF op='template.update' THEN
  IF jsonb_typeof(p_body->'is_active') NOT IN ('boolean','null') THEN
   RAISE EXCEPTION 'hr_template_invalid' USING ERRCODE='23514'; END IF;
  active:=coalesce((p_body->>'is_active')::boolean,cur.is_active);
  UPDATE public.hr_process_templates t SET name=tname, is_active=active, version=t.version+1,
   updated_at=clock_timestamp()
  WHERE t.client_id=p_client AND t.id=target RETURNING t.version INTO v;
  verb:='updated';
 ELSE
  UPDATE public.hr_process_templates t SET is_active=false, version=t.version+1,
   updated_at=clock_timestamp()
  WHERE t.client_id=p_client AND t.id=target RETURNING t.version INTO v;
  verb:='deactivated';
 END IF;
 IF op IN ('template.create','template.update') THEN
  PERFORM public.hr_template_write_rules(p_client,target,p_body->'rules');
  PERFORM public.hr_template_write_steps(p_client,target,tkind,p_body->'steps');
 END IF;
 INSERT INTO public.console_audit(actor_id,client_id,action,target_id,details)
 SELECT actor,p_client,'hr.template_'||verb,target::text,
  jsonb_build_object('template_id',t.id,'kind',t.kind,'name',t.name,'version',t.version,
                     'is_active',t.is_active)
 FROM public.hr_process_templates t WHERE t.client_id=p_client AND t.id=target;
 result:=jsonb_build_object('operation',op,'template_id',target,'version',v,
  'conflicts',public.hr_template_conflicts(p_client,target),
  'warnings',public.hr_template_warnings(p_client,target),
  'status','saved','duplicate',false);
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
 VALUES(p_client,actor,p_key,op,p_fingerprint,result);
 RETURN result;
END $$;

-- The templates with their rules and step count, for the HR team; no rows for anyone else.
CREATE OR REPLACE FUNCTION public.hr_template_rows(p_client uuid) RETURNS TABLE(
    template_id uuid, kind text, name text, version integer, is_active boolean, department_id uuid,
    employment_type text, legal_entity_reference text, region text, step_count integer,
    created_at timestamptz, updated_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF public.hr_people_viewer(p_client) IS DISTINCT FROM 'hr' THEN RETURN; END IF;
 RETURN QUERY
 SELECT t.id, t.kind, t.name, t.version, t.is_active, r.department_id, r.employment_type,
  r.legal_entity_reference, r.region,
  (SELECT count(*)::integer FROM public.hr_process_template_steps s
   WHERE s.client_id=t.client_id AND s.template_id=t.id),
  t.created_at, t.updated_at
 FROM public.hr_process_templates t
 LEFT JOIN public.hr_process_template_rules r ON r.client_id=t.client_id AND r.template_id=t.id
 WHERE t.client_id=p_client
 ORDER BY t.kind, lower(t.name), t.created_at, t.id
 LIMIT 500;
END $$;

-- One template with its rules, steps, conflicts and warnings, for the HR team; NULL for anyone
-- else and for an id that is not a template of this tenant.
CREATE OR REPLACE FUNCTION public.hr_template_get(p_client uuid, p_template uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE result jsonb;
BEGIN
 IF public.hr_people_viewer(p_client) IS DISTINCT FROM 'hr' THEN RETURN NULL; END IF;
 SELECT jsonb_build_object('template_id',t.id,'kind',t.kind,'name',t.name,'version',t.version,
  'is_active',t.is_active,'created_at',t.created_at,'updated_at',t.updated_at,
  'rules',jsonb_build_object('department_id',r.department_id,'employment_type',r.employment_type,
   'legal_entity_reference',r.legal_entity_reference,'region',r.region),
  'steps',(SELECT coalesce(jsonb_agg(jsonb_build_object('ordinal',s.ordinal,'title_en',s.title_en,
     'title_ms',s.title_ms,'description_en',s.description_en,'description_ms',s.description_ms,
     'owner_role',s.owner_role,'owner_employee_id',s.owner_employee_id,'anchor',s.anchor,
     'due_offset_days',s.due_offset_days,'required',s.required,'approval_by',s.approval_by,
     'auto_check',s.auto_check,'reference_note',s.reference_note) ORDER BY s.ordinal),'[]'::jsonb)
   FROM public.hr_process_template_steps s WHERE s.client_id=t.client_id AND s.template_id=t.id),
  'conflicts',public.hr_template_conflicts(p_client,t.id),
  'warnings',public.hr_template_warnings(p_client,t.id))
 INTO result
 FROM public.hr_process_templates t
 LEFT JOIN public.hr_process_template_rules r ON r.client_id=t.client_id AND r.template_id=t.id
 WHERE t.client_id=p_client AND t.id=p_template;
 RETURN result;
END $$;

-- Spec 5.1 and 5.7.8. NULL for a person who is not an employee of p_client.
CREATE OR REPLACE FUNCTION public.hr_process_match(p_client uuid, p_employee uuid, p_kind text)
RETURNS uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE e public.hr_employees%ROWTYPE; chosen uuid;
BEGIN
 SELECT * INTO e FROM public.hr_employees x WHERE x.client_id=p_client AND x.id=p_employee;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT t.id INTO chosen
 FROM public.hr_process_templates t
 LEFT JOIN public.hr_process_template_rules r ON r.client_id=t.client_id AND r.template_id=t.id
 WHERE t.client_id=p_client AND t.kind=p_kind AND t.is_active
  AND (r.department_id IS NULL OR r.department_id=e.department_id)
  AND (r.employment_type IS NULL OR r.employment_type=e.employment_type)
  AND (r.legal_entity_reference IS NULL
       OR lower(r.legal_entity_reference)=lower(btrim(e.legal_entity_reference)))
  AND (r.region IS NULL OR r.region=e.region)
 ORDER BY num_nonnulls(r.department_id,r.employment_type,r.legal_entity_reference,r.region) DESC,
  t.created_at, t.id
 LIMIT 1;
 RETURN chosen;
END $$;

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_template_write_rules(uuid,uuid,jsonb)'::regprocedure,
  'public.hr_template_write_steps(uuid,uuid,text,jsonb)'::regprocedure,
  'public.hr_template_conflicts(uuid,uuid)'::regprocedure,
  'public.hr_template_warnings(uuid,uuid)'::regprocedure,
  'public.hr_process_match(uuid,uuid,text)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance',f);
 END LOOP;
 FOREACH f IN ARRAY ARRAY['public.hr_template_apply(uuid,uuid,text,jsonb)'::regprocedure,
  'public.hr_template_rows(uuid)'::regprocedure,'public.hr_template_get(uuid,uuid)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;
