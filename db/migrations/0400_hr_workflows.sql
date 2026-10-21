-- Recruitment guides, attendance/corrections, leave and payroll preparation.
-- No hiring/ranking, wage deduction, payment or filing automation.
-- Precondition 0380/0390. Rollback disable routes; retain immutable event history.
CREATE TABLE public.hr_workflow_grants (
    client_id uuid NOT NULL, user_id uuid NOT NULL,
    domain text NOT NULL CHECK(domain IN ('recruitment','attendance','leave','payroll')),
    action text NOT NULL CHECK(action IN ('read','prepare','decide')),
    scope text NOT NULL CHECK(scope IN ('self','assigned_manager','hr_operator','client_owner',
                                      'payroll_operator','payroll_reviewer')),
    PRIMARY KEY(client_id,user_id,domain,action,scope),
    FOREIGN KEY(user_id,client_id) REFERENCES public.console_memberships(user_id,client_id),
    CHECK ((domain='payroll' AND ((action IN ('read','prepare') AND scope='payroll_operator')
                              OR (action='decide' AND scope='payroll_reviewer')))
        OR (domain='recruitment' AND ((action IN ('read','prepare') AND scope IN ('hr_operator','client_owner'))
                                 OR (action='decide' AND scope='client_owner')))
        OR (domain IN ('attendance','leave') AND scope IN ('self','assigned_manager','hr_operator','client_owner')
            AND (action<>'decide' OR scope IN ('assigned_manager','client_owner'))))
);
CREATE TABLE public.hr_cases (
    client_id uuid NOT NULL, id uuid NOT NULL, domain text NOT NULL,
    employee_id uuid, created_by uuid NOT NULL, payload jsonb NOT NULL,
    status text NOT NULL CHECK(status IN ('pending','approved','rejected','withdrawn')),
    version integer NOT NULL CHECK(version>=0), updated_at timestamptz NOT NULL,
    decision_reason text CHECK(length(decision_reason) BETWEEN 1 AND 500),
    CHECK(status<>'rejected' OR decision_reason IS NOT NULL),
    CHECK(status<>'pending' OR decision_reason IS NULL),
    PRIMARY KEY(client_id,id),
    FOREIGN KEY(client_id,employee_id) REFERENCES public.hr_employees(client_id,id),
    FOREIGN KEY(created_by,client_id) REFERENCES public.console_memberships(user_id,client_id),
    CHECK(domain IN ('recruitment','attendance','leave','payroll')),
    CHECK(jsonb_typeof(payload)='object' AND payload ? 'domain' AND payload->>'domain'=domain),
    CHECK((domain IN ('attendance','leave'))=(employee_id IS NOT NULL))
);
CREATE TABLE public.hr_case_events (
    client_id uuid NOT NULL,case_id uuid NOT NULL,version integer NOT NULL,
    subject_id uuid NOT NULL,request_key uuid NOT NULL,fingerprint text NOT NULL,
    action text NOT NULL CHECK(action IN ('prepare','approve','reject','withdraw')),
    status text NOT NULL,occurred_at timestamptz NOT NULL,
    PRIMARY KEY(client_id,case_id,version),UNIQUE(client_id,subject_id,request_key),
    FOREIGN KEY(client_id,case_id) REFERENCES public.hr_cases(client_id,id),
    FOREIGN KEY(subject_id,client_id) REFERENCES public.console_memberships(user_id,client_id),
    CHECK(fingerprint~'^[0-9a-f]{64}$'),
    CHECK((action='prepare' AND status='pending' AND version=0)
       OR (action='approve' AND status='approved') OR (action='reject' AND status='rejected')
       OR (action='withdraw' AND status='withdrawn'))
);

CREATE FUNCTION public.hr_case_access(p_client uuid,p_domain text,p_action text,p_employee uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE g public.hr_workflow_grants%ROWTYPE; actor uuid; manager uuid; active boolean; owner_flag boolean;
BEGIN
    IF p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid
       OR nullif(current_setting('app.hr_user_id',true),'') IS NULL THEN RETURN false; END IF;
    SELECT employee_id INTO actor FROM public.hr_employee_accounts
        WHERE client_id=p_client AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid FOR SHARE;
    PERFORM 1 FROM public.hr_client_owners WHERE client_id=p_client
        AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid FOR SHARE;
    owner_flag:=FOUND;
    IF p_employee IS NOT NULL THEN
        SELECT manager_id,is_active INTO manager,active FROM public.hr_employees
            WHERE client_id=p_client AND id=p_employee FOR SHARE;
        IF NOT FOUND OR NOT active THEN RETURN false; END IF;
        IF p_action='decide' THEN
            SELECT is_active INTO active FROM public.hr_employees
                WHERE client_id=p_client AND id=manager FOR SHARE;
            IF NOT FOUND OR NOT active THEN RETURN false; END IF;
        END IF;
    END IF;
    FOR g IN SELECT * FROM public.hr_workflow_grants WHERE client_id=p_client
        AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid
        AND domain=p_domain AND action=p_action ORDER BY scope FOR SHARE LOOP
        IF g.scope IN ('payroll_operator','payroll_reviewer') THEN RETURN true; END IF;
        IF g.scope='client_owner' AND owner_flag THEN RETURN true; END IF;
        IF g.scope='hr_operator' THEN RETURN true; END IF;
        IF g.scope='self' AND actor=p_employee THEN RETURN true; END IF;
        IF g.scope='assigned_manager' AND actor=manager
            AND (actor<>p_employee OR owner_flag) THEN RETURN true; END IF;
    END LOOP;
    RETURN false;
END $$;
REVOKE ALL ON FUNCTION public.hr_case_access(uuid,text,text,uuid) FROM PUBLIC,csa_identity,csa_ro;
GRANT EXECUTE ON FUNCTION public.hr_case_access(uuid,text,text,uuid) TO csa_app;
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['hr_workflow_grants','hr_cases','hr_case_events'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,csa_app,csa_identity,csa_ro',t);
    END LOOP;
END $$;
GRANT SELECT ON public.hr_workflow_grants TO csa_app;
CREATE POLICY hr_workflow_grant_self ON public.hr_workflow_grants FOR SELECT TO csa_app USING(
    client_id=nullif(current_setting('app.client_id',true),'')::uuid
    AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON public.hr_cases TO csa_app;
CREATE POLICY hr_case_scope ON public.hr_cases FOR SELECT TO csa_app USING(
    public.hr_case_access(client_id,domain,'read',employee_id)
    OR public.hr_case_access(client_id,domain,'prepare',employee_id)
    OR public.hr_case_access(client_id,domain,'decide',employee_id)
);
CREATE POLICY hr_case_prepare ON public.hr_cases FOR INSERT TO csa_app WITH CHECK(
    client_id=nullif(current_setting('app.client_id',true),'')::uuid
    AND created_by=nullif(current_setting('app.hr_user_id',true),'')::uuid
    AND public.hr_case_access(client_id,domain,'prepare',employee_id)
);
CREATE POLICY hr_case_decide ON public.hr_cases FOR UPDATE TO csa_app USING(
    public.hr_case_access(client_id,domain,'decide',employee_id)
    OR (created_by=nullif(current_setting('app.hr_user_id',true),'')::uuid
        AND public.hr_case_access(client_id,domain,'prepare',employee_id))
) WITH CHECK(
    (status IN ('approved','rejected') AND public.hr_case_access(client_id,domain,'decide',employee_id))
    OR (status='withdrawn' AND created_by=nullif(current_setting('app.hr_user_id',true),'')::uuid
        AND public.hr_case_access(client_id,domain,'prepare',employee_id))
);
GRANT SELECT,INSERT ON public.hr_case_events TO csa_app;
CREATE POLICY hr_case_event_self ON public.hr_case_events TO csa_app USING(
    client_id=nullif(current_setting('app.client_id',true),'')::uuid
    AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid
) WITH CHECK(client_id=nullif(current_setting('app.client_id',true),'')::uuid
    AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid);
CREATE TRIGGER hr_case_event_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.hr_case_events
    FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();
CREATE FUNCTION public.hr_case_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'pending' OR NEW.version<>0 THEN RAISE EXCEPTION 'hr_case_initial' USING ERRCODE='23514'; END IF;
    ELSIF (NEW.client_id,NEW.id,NEW.domain,NEW.employee_id,NEW.created_by,NEW.payload) IS DISTINCT FROM
          (OLD.client_id,OLD.id,OLD.domain,OLD.employee_id,OLD.created_by,OLD.payload)
        OR OLD.status<>'pending' OR NEW.status NOT IN ('approved','rejected','withdrawn')
        OR NEW.version::bigint<>OLD.version::bigint+1 OR NEW.updated_at<OLD.updated_at
    THEN RAISE EXCEPTION 'hr_case_transition' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.hr_case_guard() FROM PUBLIC,csa_identity,csa_ro;
CREATE TRIGGER hr_case_transition BEFORE INSERT OR UPDATE ON public.hr_cases
    FOR EACH ROW EXECUTE FUNCTION public.hr_case_guard();
CREATE FUNCTION public.hr_case_require_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.hr_case_events e WHERE e.client_id=NEW.client_id
       AND e.case_id=NEW.id AND e.version=NEW.version AND e.status=NEW.status
       AND e.occurred_at=NEW.updated_at) THEN
        RAISE EXCEPTION 'hr_case_audit_required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.hr_case_require_event() FROM PUBLIC,csa_app,csa_identity,csa_ro;
CREATE CONSTRAINT TRIGGER hr_case_audit_required AFTER INSERT OR UPDATE ON public.hr_cases
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.hr_case_require_event();
