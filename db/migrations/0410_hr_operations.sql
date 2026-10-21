-- Precondition: 0400 and console identity. Local HR operations; no provider sends.
-- Rollback: disable routes/switches; retain private records and immutable receipts.
ALTER TABLE public.hr_client_owners ADD COLUMN approval_reference text;
ALTER TABLE public.hr_employees ADD COLUMN version integer NOT NULL DEFAULT 0 CHECK(version>=0);
ALTER TABLE public.hr_employees ADD COLUMN region text CHECK(region IN ('peninsular_labuan','sabah','sarawak'));
ALTER TABLE public.hr_employees ADD COLUMN legal_entity_reference text;
ALTER TABLE public.hr_employees ADD COLUMN employment_category text;
CREATE TABLE public.hr_private_grants(
 client_id uuid NOT NULL,user_id uuid NOT NULL,area text NOT NULL CHECK(area IN ('recruitment','attendance','payroll')),
 approved_by uuid NOT NULL REFERENCES public.console_users(id),PRIMARY KEY(client_id,user_id,area),
 FOREIGN KEY(user_id,client_id) REFERENCES public.console_memberships(user_id,client_id));
CREATE FUNCTION public.hr_is_owner(p_client uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN RETURN false; END IF;
 PERFORM 1 FROM public.hr_client_owners WHERE client_id=p_client
  AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid FOR SHARE;
 RETURN FOUND;
END $$;
CREATE FUNCTION public.hr_private_access(p_client uuid,p_area text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN RETURN false; END IF;
 PERFORM 1 FROM public.hr_private_grants WHERE client_id=p_client AND area=p_area
  AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid FOR SHARE;
 RETURN FOUND;
END $$;
CREATE TABLE public.hr_operations(
 client_id uuid NOT NULL,subject_id uuid NOT NULL,request_key uuid NOT NULL,
 operation text NOT NULL,fingerprint text NOT NULL CHECK(fingerprint~'^[0-9a-f]{64}$'),
 receipt jsonb NOT NULL CHECK(jsonb_typeof(receipt)='object'),occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(client_id,subject_id,request_key),
 FOREIGN KEY(subject_id,client_id) REFERENCES public.console_memberships(user_id,client_id));
CREATE TRIGGER hr_operation_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.hr_operations
 FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();
CREATE TABLE public.hr_candidates(
 client_id uuid NOT NULL REFERENCES public.clients(id),id uuid NOT NULL,consent_id uuid NOT NULL,
 display_name text,cv_text text,consent_evidence text,notes jsonb NOT NULL DEFAULT '[]',
 recorded_by uuid NOT NULL REFERENCES public.console_users(id),retain_until timestamptz NOT NULL,
 erased_at timestamptz,version integer NOT NULL DEFAULT 0 CHECK(version>=0),PRIMARY KEY(client_id,id),
 CHECK((erased_at IS NULL AND display_name IS NOT NULL AND cv_text IS NOT NULL AND consent_evidence IS NOT NULL)
  OR (erased_at IS NOT NULL AND display_name IS NULL AND cv_text IS NULL AND consent_evidence IS NULL AND notes='[]')));
CREATE TABLE public.hr_interviews(
 client_id uuid NOT NULL,id uuid NOT NULL,candidate_id uuid NOT NULL,interviewer_id uuid NOT NULL,
 starts_at timestamptz NOT NULL,ends_at timestamptz NOT NULL,timezone text NOT NULL,
 status text NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled','cancelled')),
 delivery text NOT NULL DEFAULT 'local_only' CHECK(delivery='local_only'),PRIMARY KEY(client_id,id),
 FOREIGN KEY(client_id,candidate_id) REFERENCES public.hr_candidates(client_id,id),
 FOREIGN KEY(interviewer_id,client_id) REFERENCES public.console_memberships(user_id,client_id),
 CHECK(ends_at>starts_at AND ends_at<=starts_at+interval '4 hours'));
CREATE TABLE public.hr_calendars(
 client_id uuid NOT NULL REFERENCES public.clients(id),region text NOT NULL,year integer NOT NULL,
 payload jsonb NOT NULL,version integer NOT NULL DEFAULT 0,PRIMARY KEY(client_id,region,year));
CREATE TABLE public.hr_leave_balances(
 client_id uuid NOT NULL,employee_id uuid NOT NULL,year integer NOT NULL,leave_type text NOT NULL,
 days numeric(6,2) NOT NULL CHECK(days>=0 AND days<=366),policy_reference text NOT NULL,
 version integer NOT NULL DEFAULT 0,PRIMARY KEY(client_id,employee_id,year,leave_type),
 FOREIGN KEY(client_id,employee_id) REFERENCES public.hr_employees(client_id,id));
CREATE TABLE public.hr_leave_usage(
 client_id uuid NOT NULL,case_id uuid NOT NULL,employee_id uuid NOT NULL,year integer NOT NULL,
 leave_type text NOT NULL,days numeric(6,2) NOT NULL CHECK(days>=0),PRIMARY KEY(client_id,case_id,year),
 FOREIGN KEY(client_id,case_id) REFERENCES public.hr_cases(client_id,id),
 FOREIGN KEY(client_id,employee_id,year,leave_type) REFERENCES public.hr_leave_balances(client_id,employee_id,year,leave_type));
CREATE TRIGGER hr_leave_usage_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.hr_leave_usage
 FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();
CREATE TABLE public.hr_shifts(
 client_id uuid NOT NULL,id uuid NOT NULL,employee_id uuid NOT NULL,
 starts_at timestamptz NOT NULL,ends_at timestamptz NOT NULL,PRIMARY KEY(client_id,id),
 FOREIGN KEY(client_id,employee_id) REFERENCES public.hr_employees(client_id,id),
 CHECK(ends_at>starts_at AND ends_at<=starts_at+interval '24 hours'));
CREATE TABLE public.hr_payroll_rules(
 client_id uuid NOT NULL REFERENCES public.clients(id),id uuid NOT NULL,payload jsonb NOT NULL,
 reviewed_by uuid NOT NULL REFERENCES public.console_users(id),PRIMARY KEY(client_id,id));
CREATE TABLE public.hr_payroll_snapshots(
 client_id uuid NOT NULL,case_id uuid NOT NULL,payload jsonb NOT NULL,digest text NOT NULL,
 prepared_by uuid NOT NULL REFERENCES public.console_users(id),reviewed_by uuid REFERENCES public.console_users(id),
 status text NOT NULL DEFAULT 'prepared' CHECK(status IN ('prepared','approved','reversed')),
 reversal_reason text,version integer NOT NULL DEFAULT 0,PRIMARY KEY(client_id,case_id),
 FOREIGN KEY(client_id,case_id) REFERENCES public.hr_cases(client_id,id));
CREATE TABLE public.hr_notifications(
 client_id uuid NOT NULL,subject_id uuid NOT NULL,id uuid NOT NULL,reminder_key text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('workflow_review','training_review','interview_upcoming')),resource_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),read_at timestamptz,
 PRIMARY KEY(client_id,id),UNIQUE(client_id,subject_id,reminder_key),
 FOREIGN KEY(subject_id,client_id) REFERENCES public.console_memberships(user_id,client_id));

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['hr_private_grants','hr_operations','hr_candidates','hr_interviews','hr_calendars',
 'hr_leave_balances','hr_leave_usage','hr_shifts','hr_payroll_rules','hr_payroll_snapshots','hr_notifications'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,csa_app,csa_identity,csa_ro',t);
 END LOOP;
END $$;
GRANT SELECT ON public.hr_private_grants TO csa_app;
CREATE POLICY hr_private_grant_self ON public.hr_private_grants FOR SELECT TO csa_app USING(
 client_id=nullif(current_setting('app.client_id',true),'')::uuid AND
 (user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid OR public.hr_is_owner(client_id)));
GRANT SELECT,INSERT ON public.hr_operations TO csa_app;
CREATE POLICY hr_operation_self ON public.hr_operations TO csa_app USING(
 client_id=nullif(current_setting('app.client_id',true),'')::uuid
 AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid) WITH CHECK(
 client_id=nullif(current_setting('app.client_id',true),'')::uuid
 AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid);
DO $$ DECLARE t text; area text; BEGIN
 FOR t,area IN VALUES ('hr_candidates','recruitment'),('hr_interviews','recruitment'),
 ('hr_payroll_rules','payroll'),('hr_payroll_snapshots','payroll') LOOP
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON public.%I TO csa_app',t);
  EXECUTE format('CREATE POLICY hr_private_scope ON public.%I TO csa_app USING(public.hr_private_access(client_id,%L)) WITH CHECK(public.hr_private_access(client_id,%L))',t,area,area);
 END LOOP;
END $$;
REVOKE UPDATE ON public.hr_payroll_rules FROM csa_app;
CREATE TRIGGER hr_payroll_rule_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.hr_payroll_rules
 FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();
GRANT SELECT,INSERT,UPDATE ON public.hr_calendars,public.hr_leave_balances,public.hr_shifts TO csa_app;
CREATE POLICY hr_calendar_read ON public.hr_calendars FOR SELECT TO csa_app USING(
 client_id=nullif(current_setting('app.client_id',true),'')::uuid AND nullif(current_setting('app.hr_user_id',true),'') IS NOT NULL);
CREATE POLICY hr_calendar_owner ON public.hr_calendars TO csa_app USING(public.hr_is_owner(client_id)) WITH CHECK(public.hr_is_owner(client_id));
CREATE POLICY hr_balance_read ON public.hr_leave_balances FOR SELECT TO csa_app USING(
 public.hr_case_access(client_id,'leave','read',employee_id) OR public.hr_case_access(client_id,'leave','decide',employee_id));
CREATE POLICY hr_balance_owner ON public.hr_leave_balances TO csa_app USING(public.hr_is_owner(client_id)) WITH CHECK(public.hr_is_owner(client_id));
CREATE POLICY hr_shift_scope ON public.hr_shifts TO csa_app USING(public.hr_private_access(client_id,'attendance')) WITH CHECK(public.hr_private_access(client_id,'attendance'));
GRANT SELECT,INSERT ON public.hr_leave_usage TO csa_app;
CREATE POLICY hr_usage_scope ON public.hr_leave_usage TO csa_app USING(
 public.hr_is_owner(client_id) OR public.hr_case_access(client_id,'leave','read',employee_id)
 OR public.hr_case_access(client_id,'leave','decide',employee_id)) WITH CHECK(
 public.hr_case_access(client_id,'leave','decide',employee_id));
GRANT SELECT,INSERT,UPDATE ON public.hr_notifications TO csa_app;
CREATE POLICY hr_notification_self ON public.hr_notifications TO csa_app USING(
 client_id=nullif(current_setting('app.client_id',true),'')::uuid
 AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid) WITH CHECK(
 client_id=nullif(current_setting('app.client_id',true),'')::uuid
 AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid);

CREATE FUNCTION public.hr_admin_apply(p_client uuid,p_key uuid,p_fingerprint text,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE actor uuid; target uuid; manager uuid; result jsonb; v integer; item jsonb; op text;
BEGIN
 actor:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 IF NOT public.hr_is_owner(p_client) THEN RAISE EXCEPTION 'hr_owner_required' USING ERRCODE='42501'; END IF;
 op:=p_body->>'operation';
 IF op='employee.save' THEN
  target:=(p_body->>'employee_id')::uuid;
  manager:=(p_body->'values'->'assigned_manager'->>'employee_id')::uuid;
  IF manager IS NOT NULL AND (p_body->'values'->'assigned_manager'->>'tenant_id')::uuid IS DISTINCT FROM p_client
   THEN RAISE EXCEPTION 'hr_cross_tenant' USING ERRCODE='23514'; END IF;
  IF p_body->>'account_id' IS NOT NULL THEN
   PERFORM 1 FROM public.console_memberships WHERE client_id=p_client AND user_id=(p_body->>'account_id')::uuid AND is_enabled FOR SHARE;
   IF NOT FOUND THEN RAISE EXCEPTION 'hr_member_unavailable' USING ERRCODE='23514'; END IF;
  END IF;
  IF p_body->>'expected_version' IS NULL THEN
   INSERT INTO public.hr_employees(client_id,id,display_name,job_title,department,work_email,manager_id,is_active,region,legal_entity_reference,employment_category)
   VALUES(p_client,target,p_body->'values'->>'display_name',p_body->'values'->>'job_title',p_body->'values'->>'department',p_body->'values'->>'work_email',manager,(p_body->>'active')::boolean,p_body->>'region',p_body->>'legal_entity_reference',p_body->>'employment_category') RETURNING version INTO v;
  ELSE
   UPDATE public.hr_employees SET display_name=p_body->'values'->>'display_name',job_title=p_body->'values'->>'job_title',department=p_body->'values'->>'department',work_email=p_body->'values'->>'work_email',manager_id=manager,is_active=(p_body->>'active')::boolean,version=version+1,region=p_body->>'region',legal_entity_reference=p_body->>'legal_entity_reference',employment_category=p_body->>'employment_category'
    WHERE client_id=p_client AND id=target AND version=(p_body->>'expected_version')::integer RETURNING version INTO v;
   IF NOT FOUND THEN RAISE EXCEPTION 'hr_stale_employee' USING ERRCODE='40001'; END IF;
  END IF;
  DELETE FROM public.hr_employee_accounts WHERE client_id=p_client AND employee_id=target;
  IF p_body->>'account_id' IS NOT NULL THEN
   INSERT INTO public.hr_employee_accounts(client_id,user_id,employee_id) VALUES(p_client,(p_body->>'account_id')::uuid,target);
  END IF;
 ELSE
  target:=(p_body->>'user_id')::uuid;
  PERFORM 1 FROM public.console_memberships WHERE client_id=p_client AND user_id=target AND is_enabled FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'hr_member_unavailable' USING ERRCODE='23514'; END IF;
  IF op='access.save' THEN
   DELETE FROM public.hr_grants WHERE client_id=p_client AND user_id=target;
   DELETE FROM public.hr_workflow_grants WHERE client_id=p_client AND user_id=target;
   FOR item IN SELECT * FROM jsonb_array_elements(p_body->'grants') LOOP
    INSERT INTO public.hr_grants(client_id,user_id,action,scope,fields) VALUES(p_client,target,item->>'action',item->>'scope',ARRAY(SELECT jsonb_array_elements_text(item->'fields')));
   END LOOP;
   FOR item IN SELECT * FROM jsonb_array_elements(p_body->'workflows') LOOP
    INSERT INTO public.hr_workflow_grants(client_id,user_id,domain,action,scope) VALUES(p_client,target,item->>'domain',item->>'action',item->>'scope');
   END LOOP;
  ELSIF op='private_access.save' THEN
   IF (p_body->>'enabled')::boolean THEN
    INSERT INTO public.hr_private_grants VALUES(p_client,target,p_body->>'area',actor) ON CONFLICT DO NOTHING;
   ELSE DELETE FROM public.hr_private_grants WHERE client_id=p_client AND user_id=target AND area=p_body->>'area'; END IF;
  ELSE RAISE EXCEPTION 'hr_operation_unsupported' USING ERRCODE='23514'; END IF;
  v:=0;
 END IF;
 result:=jsonb_build_object('operation',op,'resource_id',target,'version',v,'status','saved','duplicate',false);
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt) VALUES(p_client,actor,p_key,op,p_fingerprint,result);
 INSERT INTO public.hr_authority_events(client_id,subject_id,request_key,operation,details)
 VALUES(p_client,actor,p_key,op,CASE WHEN op='employee.save' THEN jsonb_build_object(
  'employee_id',target,'manager_id',manager,'account_id',p_body->'account_id','active',p_body->'active','version',v)
 ELSE p_body END);
 RETURN result;
END $$;
CREATE FUNCTION public.hr_admin_employees(p_client uuid) RETURNS SETOF jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF NOT public.hr_is_owner(p_client) THEN RETURN; END IF;
 RETURN QUERY SELECT jsonb_build_object('employee_id',e.id,'display_name',e.display_name,'version',e.version,
  'job_title',e.job_title,'department',e.department,'work_email',e.work_email,
  'active',e.is_active,'manager_id',e.manager_id,'account_id',a.user_id,'region',e.region,
  'legal_entity_reference',e.legal_entity_reference,'employment_category',e.employment_category)
 FROM public.hr_employees e LEFT JOIN public.hr_employee_accounts a ON a.client_id=e.client_id AND a.employee_id=e.id
 WHERE e.client_id=p_client ORDER BY e.id LIMIT 200;
END $$;
CREATE FUNCTION public.hr_candidate_available(p_client uuid,p_candidate uuid,p_consent uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF NOT public.hr_case_access(p_client,'recruitment','prepare',NULL) THEN RETURN false; END IF;
 PERFORM 1 FROM public.hr_candidates WHERE client_id=p_client AND id=p_candidate AND consent_id=p_consent
 AND erased_at IS NULL AND retain_until>clock_timestamp() FOR SHARE;
 RETURN FOUND;
END $$;
CREATE FUNCTION public.hr_employment_matches(p_client uuid,p_employee uuid,p_region text,p_entity text,p_category text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF NOT public.hr_private_access(p_client,'payroll') THEN RETURN false; END IF;
 PERFORM 1 FROM public.hr_employees WHERE client_id=p_client AND id=p_employee AND is_active
  AND region=p_region AND legal_entity_reference=p_entity AND employment_category=p_category FOR SHARE;
 RETURN FOUND;
END $$;
CREATE FUNCTION public.hr_import_employee(p_client uuid,p_employee uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF NOT public.hr_private_access(p_client,'attendance') THEN RETURN false; END IF;
 PERFORM 1 FROM public.hr_employees WHERE client_id=p_client AND id=p_employee AND is_active FOR SHARE;
 RETURN FOUND;
END $$;
-- Explicit attendance delegates may prepare imported records, without approving them.
CREATE POLICY hr_import_case_read ON public.hr_cases FOR SELECT TO csa_app USING(
 domain='attendance' AND public.hr_private_access(client_id,'attendance'));
CREATE POLICY hr_import_case_prepare ON public.hr_cases FOR INSERT TO csa_app WITH CHECK(
 domain='attendance' AND created_by=nullif(current_setting('app.hr_user_id',true),'')::uuid
 AND public.hr_private_access(client_id,'attendance'));
DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_is_owner(uuid)'::regprocedure,'public.hr_private_access(uuid,text)'::regprocedure,
 'public.hr_admin_apply(uuid,uuid,text,jsonb)'::regprocedure,'public.hr_admin_employees(uuid)'::regprocedure,
 'public.hr_candidate_available(uuid,uuid,uuid)'::regprocedure,
 'public.hr_import_employee(uuid,uuid)'::regprocedure,
 'public.hr_employment_matches(uuid,uuid,text,text,text)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;

CREATE FUNCTION public.hr_interviewer_available(p_client uuid,p_user uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF NOT public.hr_private_access(p_client,'recruitment') THEN RETURN false; END IF;
 PERFORM 1 FROM public.console_memberships m JOIN public.console_users u ON u.id=m.user_id
 WHERE m.client_id=p_client AND m.user_id=p_user AND m.is_enabled AND u.is_enabled FOR SHARE OF m,u;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION public.hr_interviewer_available(uuid,uuid) FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_interviewer_available(uuid,uuid) TO csa_app;

CREATE FUNCTION public.hr_leave_balance_lock(p_client uuid,p_employee uuid,p_year integer,p_type text)
RETURNS TABLE(days numeric,version integer) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF NOT(public.hr_is_owner(p_client) OR public.hr_case_access(p_client,'leave','decide',p_employee)) THEN RETURN; END IF;
 RETURN QUERY SELECT b.days,b.version FROM public.hr_leave_balances b WHERE b.client_id=p_client
 AND b.employee_id=p_employee AND b.year=p_year AND b.leave_type=p_type FOR UPDATE;
END $$;
CREATE FUNCTION public.hr_calendar_lock(p_client uuid,p_region text,p_year integer)
RETURNS TABLE(payload jsonb,version integer) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid
 OR nullif(current_setting('app.hr_user_id',true),'') IS NULL THEN RETURN; END IF;
 RETURN QUERY SELECT c.payload,c.version FROM public.hr_calendars c WHERE c.client_id=p_client
 AND c.region=p_region AND c.year=p_year FOR SHARE;
END $$;
REVOKE ALL ON FUNCTION public.hr_leave_balance_lock(uuid,uuid,integer,text),public.hr_calendar_lock(uuid,text,integer) FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_leave_balance_lock(uuid,uuid,integer,text),public.hr_calendar_lock(uuid,text,integer) TO csa_app;

ALTER TABLE public.hr_agent_settings DROP CONSTRAINT hr_agent_settings_name_check;
ALTER TABLE public.hr_agent_settings ADD CHECK(name IN ('hr_directory','hr_training','hr_recruitment','hr_attendance','hr_payroll','hr_assistant'));
INSERT INTO public.hr_agent_settings(client_id,name) SELECT id,'hr_assistant' FROM public.clients;
CREATE OR REPLACE FUNCTION public.hr_new_client_agents() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 INSERT INTO public.hr_agent_settings(client_id,name) SELECT NEW.id,n FROM unnest(ARRAY[
 'hr_directory','hr_training','hr_recruitment','hr_attendance','hr_payroll','hr_assistant']) n;
 RETURN NEW;
END $$;
CREATE TABLE public.hr_chat_plans(
 client_id uuid NOT NULL,subject_id uuid NOT NULL,request_key uuid NOT NULL,
 session_id uuid NOT NULL REFERENCES public.console_sessions(id),fingerprint text NOT NULL,
 plan jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(client_id,subject_id,request_key),
 FOREIGN KEY(subject_id,client_id) REFERENCES public.console_memberships(user_id,client_id));
ALTER TABLE public.hr_chat_plans ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.hr_chat_plans FROM PUBLIC,csa_app,csa_ro,csa_identity;
GRANT SELECT,INSERT ON public.hr_chat_plans TO csa_app;
CREATE POLICY hr_chat_self ON public.hr_chat_plans TO csa_app USING(
 client_id=nullif(current_setting('app.client_id',true),'')::uuid
 AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid) WITH CHECK(
 client_id=nullif(current_setting('app.client_id',true),'')::uuid
 AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid);
CREATE TRIGGER hr_chat_plan_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.hr_chat_plans
 FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();

CREATE FUNCTION public.hr_admin_members(p_client uuid) RETURNS TABLE(user_id uuid,display_name text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF NOT public.hr_is_owner(p_client) THEN RETURN; END IF;
 RETURN QUERY SELECT u.id,u.display_name FROM public.console_users u JOIN public.console_memberships m ON m.user_id=u.id
 WHERE m.client_id=p_client AND m.is_enabled AND u.is_enabled ORDER BY u.id LIMIT 200;
END $$;
REVOKE ALL ON FUNCTION public.hr_admin_members(uuid) FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_admin_members(uuid) TO csa_app;

CREATE POLICY hr_interview_self_read ON public.hr_interviews FOR SELECT TO csa_app USING(
 client_id=nullif(current_setting('app.client_id',true),'')::uuid
 AND interviewer_id=nullif(current_setting('app.hr_user_id',true),'')::uuid AND status='scheduled');

REVOKE UPDATE ON public.hr_shifts FROM csa_app;
REVOKE UPDATE ON public.hr_payroll_snapshots FROM csa_app;
GRANT UPDATE(status,version,reviewed_by,reversal_reason) ON public.hr_payroll_snapshots TO csa_app;
CREATE FUNCTION public.hr_payroll_snapshot_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF (NEW.client_id,NEW.case_id,NEW.payload,NEW.digest,NEW.prepared_by) IS DISTINCT FROM
 (OLD.client_id,OLD.case_id,OLD.payload,OLD.digest,OLD.prepared_by)
 OR NEW.version::bigint<>OLD.version::bigint+1
 OR NOT public.hr_case_access(NEW.client_id,'payroll','decide',NULL)
 OR NEW.prepared_by=nullif(current_setting('app.hr_user_id',true),'')::uuid THEN
 RAISE EXCEPTION 'hr_payroll_immutable_snapshot' USING ERRCODE='23514'; END IF;
 IF OLD.status='prepared' AND NEW.status='approved' AND NEW.reviewed_by=nullif(current_setting('app.hr_user_id',true),'')::uuid
 AND NEW.reversal_reason IS NULL THEN RETURN NEW; END IF;
 IF OLD.status='approved' AND NEW.status='reversed' AND length(btrim(NEW.reversal_reason)) BETWEEN 1 AND 500
 AND NEW.reviewed_by=OLD.reviewed_by THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'hr_payroll_invalid_transition' USING ERRCODE='23514';
END $$;
REVOKE ALL ON FUNCTION public.hr_payroll_snapshot_guard() FROM PUBLIC,csa_ro,csa_identity;
CREATE TRIGGER hr_payroll_snapshot_guard BEFORE UPDATE ON public.hr_payroll_snapshots
 FOR EACH ROW EXECUTE FUNCTION public.hr_payroll_snapshot_guard();

-- Explicit system principal for nonfinancial retention/reminders. NOLOGIN until
-- an operator deliberately provisions the isolated worker credential.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='csa_hr_maintenance') THEN
 CREATE ROLE csa_hr_maintenance NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO csa_hr_maintenance;
CREATE TABLE public.hr_automation_settings(
 client_id uuid PRIMARY KEY REFERENCES public.clients(id),enabled boolean NOT NULL DEFAULT false,
 remind_after_hours integer NOT NULL CHECK(remind_after_hours BETWEEN 1 AND 720),
 approved_by uuid NOT NULL REFERENCES public.console_users(id));
CREATE TABLE public.hr_maintenance_runs(
 client_id uuid NOT NULL REFERENCES public.clients(id),id uuid NOT NULL DEFAULT gen_random_uuid(),
 erased_count integer NOT NULL CHECK(erased_count>=0),notification_count integer NOT NULL CHECK(notification_count>=0),
 completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(client_id,id));
ALTER TABLE public.hr_automation_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hr_maintenance_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.hr_automation_settings,public.hr_maintenance_runs FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance;
GRANT SELECT,INSERT,UPDATE ON public.hr_automation_settings TO csa_app;
GRANT SELECT ON public.hr_maintenance_runs TO csa_app;
CREATE POLICY hr_automation_owner ON public.hr_automation_settings TO csa_app USING(public.hr_is_owner(client_id)) WITH CHECK(public.hr_is_owner(client_id));
CREATE POLICY hr_maintenance_owner ON public.hr_maintenance_runs FOR SELECT TO csa_app USING(public.hr_is_owner(client_id));
CREATE TRIGGER hr_maintenance_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.hr_maintenance_runs
 FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();
CREATE FUNCTION public.hr_maintenance_tenants() RETURNS TABLE(client_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF session_user<>'csa_hr_maintenance' THEN RAISE EXCEPTION 'hr_worker_role_required' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT a.client_id FROM public.hr_automation_settings a WHERE a.enabled ORDER BY a.client_id;
END $$;
CREATE FUNCTION public.hr_maintain_due(p_client uuid) RETURNS TABLE(status text,erased_count integer,notification_count integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE cfg public.hr_automation_settings%ROWTYPE; recipient uuid; stamp timestamptz; bucket text; n integer; erased integer:=0; notified integer:=0;
BEGIN
 IF session_user<>'csa_hr_maintenance' THEN RAISE EXCEPTION 'hr_worker_role_required' USING ERRCODE='42501'; END IF;
 IF NOT pg_try_advisory_xact_lock(hashtextextended(p_client::text || ':hr-operations',0)) THEN
 RETURN QUERY SELECT 'busy'::text,0,0; RETURN; END IF;
 SELECT * INTO cfg FROM public.hr_automation_settings WHERE client_id=p_client AND enabled FOR SHARE;
 IF NOT FOUND THEN RETURN QUERY SELECT 'disabled'::text,0,0; RETURN; END IF;
 stamp:=clock_timestamp();bucket:=(stamp AT TIME ZONE 'Asia/Kuala_Lumpur')::date::text;
 PERFORM set_config('app.client_id',p_client::text,true);
 UPDATE public.hr_candidates SET display_name=NULL,cv_text=NULL,consent_evidence=NULL,notes='[]',erased_at=stamp,version=version+1
 WHERE client_id=p_client AND erased_at IS NULL AND retain_until<=stamp;
 GET DIAGNOSTICS erased=ROW_COUNT;
 UPDATE public.hr_interviews i SET status='cancelled' WHERE i.client_id=p_client AND i.status='scheduled'
 AND EXISTS(SELECT 1 FROM public.hr_candidates c WHERE c.client_id=i.client_id AND c.id=i.candidate_id AND c.erased_at IS NOT NULL);
 FOR recipient IN SELECT m.user_id FROM public.console_memberships m JOIN public.console_users u ON u.id=m.user_id
 WHERE m.client_id=p_client AND m.is_enabled AND u.is_enabled AND NOT u.must_change_password FOR SHARE OF m,u LOOP
  -- This is an explicitly authorized maintenance principal selecting recipients,
  -- not a human session or a business-action impersonation path.
  PERFORM set_config('app.hr_user_id',recipient::text,true);
  INSERT INTO public.hr_notifications(client_id,subject_id,id,reminder_key,kind,resource_id)
  SELECT c.client_id,recipient,gen_random_uuid(),c.id::text || ':' || bucket,'workflow_review',c.id
  FROM public.hr_cases c WHERE c.client_id=p_client AND c.status='pending'
  AND c.updated_at<=stamp-make_interval(hours=>cfg.remind_after_hours)
  AND public.hr_case_access(c.client_id,c.domain,'decide',c.employee_id)
  ON CONFLICT(client_id,subject_id,reminder_key) DO NOTHING;
  GET DIAGNOSTICS n=ROW_COUNT;notified:=notified+n;
  INSERT INTO public.hr_notifications(client_id,subject_id,id,reminder_key,kind,resource_id)
  SELECT r.client_id,recipient,gen_random_uuid(),r.id::text || ':' || bucket,'training_review',r.id
  FROM public.hr_training_requests r CROSS JOIN LATERAL public.hr_lock_employee(r.client_id,r.employee_id) e
  WHERE r.client_id=p_client AND r.status='submitted' AND r.updated_at<=stamp-make_interval(hours=>cfg.remind_after_hours)
  AND e.manager_active AND EXISTS(SELECT 1 FROM public.hr_grants g LEFT JOIN public.hr_employee_accounts a ON a.client_id=g.client_id AND a.user_id=g.user_id
   WHERE g.client_id=r.client_id AND g.user_id=recipient AND g.action='training.decide'
   AND ((g.scope='client_owner' AND public.hr_is_owner(g.client_id)) OR
   (g.scope='assigned_manager' AND a.employee_id=e.manager_id AND (a.employee_id<>r.employee_id OR public.hr_is_owner(g.client_id)))))
  ON CONFLICT(client_id,subject_id,reminder_key) DO NOTHING;
  GET DIAGNOSTICS n=ROW_COUNT;notified:=notified+n;
  INSERT INTO public.hr_notifications(client_id,subject_id,id,reminder_key,kind,resource_id)
  SELECT i.client_id,recipient,gen_random_uuid(),i.id::text || ':interview','interview_upcoming',i.id
  FROM public.hr_interviews i WHERE i.client_id=p_client AND i.interviewer_id=recipient AND i.status='scheduled'
  AND i.starts_at>stamp AND i.starts_at<=stamp+interval '1 day'
  AND EXISTS(SELECT 1 FROM public.hr_candidates c WHERE c.client_id=i.client_id AND c.id=i.candidate_id AND c.erased_at IS NULL AND c.retain_until>stamp)
  ON CONFLICT(client_id,subject_id,reminder_key) DO NOTHING;
  GET DIAGNOSTICS n=ROW_COUNT;notified:=notified+n;
 END LOOP;
 INSERT INTO public.hr_maintenance_runs(client_id,erased_count,notification_count) VALUES(p_client,erased,notified);
 RETURN QUERY SELECT 'completed'::text,erased,notified;
END $$;
REVOKE ALL ON FUNCTION public.hr_maintenance_tenants(),public.hr_maintain_due(uuid) FROM PUBLIC,csa_app,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_maintenance_tenants(),public.hr_maintain_due(uuid) TO csa_hr_maintenance;
ALTER TABLE public.hr_shifts ADD COLUMN status text NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled','cancelled'));
ALTER TABLE public.hr_shifts ADD COLUMN cancellation_reason text;
ALTER TABLE public.hr_interviews ADD COLUMN cancellation_reason text;
GRANT UPDATE(status,cancellation_reason) ON public.hr_shifts TO csa_app;

ALTER TABLE public.hr_private_grants ADD FOREIGN KEY(approved_by,client_id) REFERENCES public.console_memberships(user_id,client_id);
ALTER TABLE public.hr_candidates ADD FOREIGN KEY(recorded_by,client_id) REFERENCES public.console_memberships(user_id,client_id);
ALTER TABLE public.hr_payroll_rules ADD FOREIGN KEY(reviewed_by,client_id) REFERENCES public.console_memberships(user_id,client_id);
ALTER TABLE public.hr_payroll_snapshots ADD FOREIGN KEY(prepared_by,client_id) REFERENCES public.console_memberships(user_id,client_id);
ALTER TABLE public.hr_payroll_snapshots ADD FOREIGN KEY(reviewed_by,client_id) REFERENCES public.console_memberships(user_id,client_id);
ALTER TABLE public.hr_automation_settings ADD FOREIGN KEY(approved_by,client_id) REFERENCES public.console_memberships(user_id,client_id);

CREATE TABLE public.hr_authority_events(
 client_id uuid NOT NULL,subject_id uuid NOT NULL,request_key uuid NOT NULL,operation text NOT NULL,
 details jsonb NOT NULL CHECK(jsonb_typeof(details)='object'),occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(client_id,subject_id,request_key),FOREIGN KEY(client_id,subject_id,request_key)
 REFERENCES public.hr_operations(client_id,subject_id,request_key));
ALTER TABLE public.hr_authority_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.hr_authority_events FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance;
GRANT SELECT,INSERT ON public.hr_authority_events TO csa_app;
CREATE POLICY hr_authority_owner ON public.hr_authority_events TO csa_app USING(public.hr_is_owner(client_id))
 WITH CHECK(public.hr_is_owner(client_id) AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid);
CREATE TRIGGER hr_authority_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.hr_authority_events
 FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();
