-- HR employee directory and training foundation, ordered source migration.
-- Precondition: baseline plus migrations through 0360 and existing console identity.
-- Never apply to persistent dev/production as part of tests or this task.
-- Rollback: keep routes disabled; revoke app grants/functions. Preserve HR/audit data.
-- Additive only; no FORCE RLS or grants to the identity role.

CREATE TABLE public.hr_employees (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    id uuid NOT NULL,
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 120),
    job_title text CHECK (length(btrim(job_title)) BETWEEN 1 AND 120),
    department text CHECK (length(btrim(department)) BETWEEN 1 AND 120),
    work_email text CHECK (length(work_email) BETWEEN 3 AND 254),
    manager_id uuid,
    is_active boolean NOT NULL DEFAULT true,
    PRIMARY KEY (client_id, id),
    FOREIGN KEY (client_id, manager_id) REFERENCES public.hr_employees(client_id, id)
);
CREATE TABLE public.hr_employee_accounts (
    client_id uuid NOT NULL,
    user_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    PRIMARY KEY (client_id, user_id),
    UNIQUE (client_id, employee_id),
    FOREIGN KEY (client_id, employee_id) REFERENCES public.hr_employees(client_id, id),
    FOREIGN KEY (user_id, client_id) REFERENCES public.console_memberships(user_id, client_id)
);
CREATE TABLE public.hr_client_owners (
    client_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    approved_by uuid NOT NULL REFERENCES public.console_users(id),
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (user_id, client_id) REFERENCES public.console_memberships(user_id, client_id)
);
CREATE TABLE public.hr_grants (
    client_id uuid NOT NULL,
    user_id uuid NOT NULL,
    action text NOT NULL,
    scope text NOT NULL,
    fields text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (client_id, user_id, action, scope),
    FOREIGN KEY (user_id, client_id) REFERENCES public.console_memberships(user_id, client_id),
    CHECK (
        (scope = 'self' AND action IN ('directory.read','training.read','training.draft',
                                     'training.submit','training.withdraw'))
        OR (scope = 'assigned_manager' AND action IN
            ('directory.read','training.read','training.decide'))
        OR (scope = 'hr_operator' AND action IN ('directory.read','training.read'))
        OR (scope = 'client_owner' AND action = 'training.decide')
    ),
    CHECK (fields <@ ARRAY['display_name','job_title','department','work_email','assigned_manager']
        AND cardinality(fields) <= 5 AND array_position(fields, NULL) IS NULL),
    CHECK (action = 'directory.read' OR cardinality(fields) = 0)
);
CREATE TABLE public.hr_training_requests (
    client_id uuid NOT NULL,
    id uuid NOT NULL,
    employee_id uuid NOT NULL,
    created_by uuid NOT NULL REFERENCES public.console_users(id),
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 160),
    learning_objective text NOT NULL CHECK (length(btrim(learning_objective)) BETWEEN 1 AND 1000),
    status text NOT NULL CHECK (status IN ('draft','submitted','approved','rejected','withdrawn')),
    version integer NOT NULL CHECK (version >= 0),
    updated_at timestamptz NOT NULL,
    decision_by uuid REFERENCES public.console_users(id),
    decision_note text CHECK (length(btrim(decision_note)) BETWEEN 1 AND 500),
    PRIMARY KEY (client_id, id),
    FOREIGN KEY (client_id, employee_id) REFERENCES public.hr_employees(client_id, id),
    FOREIGN KEY (created_by, client_id) REFERENCES public.console_memberships(user_id, client_id),
    CHECK ((status IN ('approved','rejected') AND decision_by IS NOT NULL)
        OR (status NOT IN ('approved','rejected') AND decision_by IS NULL AND decision_note IS NULL)),
    CHECK (status <> 'rejected' OR decision_note IS NOT NULL)
);
CREATE TABLE public.hr_training_receipts (
    client_id uuid NOT NULL,
    subject_id uuid NOT NULL REFERENCES public.console_users(id),
    request_key uuid NOT NULL,
    request_id uuid NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    action text NOT NULL CHECK (action IN ('save_draft','submit','withdraw','approve','reject')),
    status text NOT NULL CHECK (status IN ('draft','submitted','approved','rejected','withdrawn')),
    version integer NOT NULL CHECK (version >= 0),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (client_id, subject_id, request_key),
    UNIQUE (client_id, request_id, version),
    FOREIGN KEY (client_id, request_id) REFERENCES public.hr_training_requests(client_id, id),
    FOREIGN KEY (subject_id, client_id) REFERENCES public.console_memberships(user_id, client_id),
    CHECK ((action = 'save_draft' AND status = 'draft' AND version = 0)
        OR (action = 'submit' AND status = 'submitted')
        OR (action = 'withdraw' AND status = 'withdrawn')
        OR (action = 'approve' AND status = 'approved')
        OR (action = 'reject' AND status = 'rejected'))
);
CREATE TABLE public.hr_training_audit (
    client_id uuid NOT NULL,
    request_id uuid NOT NULL,
    subject_id uuid NOT NULL,
    request_key uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('save_draft','submit','withdraw','approve','reject')),
    before_status text CHECK (before_status IN ('draft','submitted')),
    after_status text NOT NULL CHECK (after_status IN
        ('draft','submitted','approved','rejected','withdrawn')),
    version integer NOT NULL CHECK (version >= 0),
    authorized_scope text NOT NULL CHECK (authorized_scope IN
        ('self','assigned_manager','client_owner')),
    manager_id uuid,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (client_id, request_id, version),
    FOREIGN KEY (client_id, subject_id, request_key)
        REFERENCES public.hr_training_receipts(client_id, subject_id, request_key),
    FOREIGN KEY (client_id, manager_id) REFERENCES public.hr_employees(client_id, id),
    CHECK (((action = 'save_draft' AND before_status IS NULL AND after_status = 'draft'
               AND version = 0 AND authorized_scope = 'self')
        OR (action = 'submit' AND before_status = 'draft' AND after_status = 'submitted'
               AND authorized_scope = 'self')
        OR (action = 'withdraw' AND before_status IN ('draft','submitted')
               AND after_status = 'withdrawn' AND authorized_scope = 'self')
        OR (action = 'approve' AND before_status = 'submitted' AND after_status = 'approved'
               AND authorized_scope IN ('assigned_manager','client_owner'))
        OR (action = 'reject' AND before_status = 'submitted' AND after_status = 'rejected'
               AND authorized_scope IN ('assigned_manager','client_owner'))) IS TRUE)
);

-- Holds identity locks until the caller's domain transaction commits. Existing
-- identity UPDATE/DELETE revokers conflict with these locks without modification.
-- Deadlock aborts fail explicitly; no retry can bypass a fresh lookup.
CREATE FUNCTION public.hr_lock_session(
    p_session uuid, p_user uuid, p_client uuid, p_token_hash text,
    p_idle integer, p_csrf_hash text
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE u public.console_users%ROWTYPE; s public.console_sessions%ROWTYPE;
        m public.console_memberships%ROWTYPE;
BEGIN
    IF session_user <> 'csa_app' OR p_idle NOT BETWEEN 60 AND 86400 THEN
        RETURN false;
    END IF;
    IF p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid
    THEN RETURN false; END IF;
    SELECT * INTO u FROM public.console_users WHERE id = p_user FOR SHARE;
    IF NOT FOUND OR NOT u.is_enabled OR u.must_change_password THEN RETURN false; END IF;
    SELECT * INTO s FROM public.console_sessions
        WHERE id = p_session AND user_id = p_user AND token_hash = p_token_hash FOR SHARE;
    IF NOT FOUND OR s.revoked_at IS NOT NULL
        OR s.expires_at <= clock_timestamp()
        OR s.last_seen_at <= clock_timestamp() - make_interval(secs => p_idle)
        OR (NOT u.is_platform_admin AND s.client_id IS DISTINCT FROM p_client)
        OR (p_csrf_hash IS NOT NULL AND s.csrf_hash IS DISTINCT FROM p_csrf_hash)
    THEN RETURN false; END IF;
    SELECT * INTO m FROM public.console_memberships
        WHERE user_id = p_user AND client_id = p_client FOR SHARE;
    IF NOT FOUND OR NOT m.is_enabled OR (p_csrf_hash IS NOT NULL AND m.role = 'read_only')
    THEN RETURN false; END IF;
    PERFORM set_config('app.hr_user_id', p_user::text, true);
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.hr_lock_session(uuid,uuid,uuid,text,integer,text)
    FROM PUBLIC, csa_identity, csa_ro;
GRANT EXECUTE ON FUNCTION public.hr_lock_session(uuid,uuid,uuid,text,integer,text) TO csa_app;

-- Resource visibility is narrower than tenant RLS. Exact action and fields are
-- also checked by the domain service. Ownership alone does not give read grants.
CREATE FUNCTION public.hr_resource_visible(p_client uuid, p_employee uuid, p_kind text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
    SELECT p_client = nullif(current_setting('app.client_id',true),'')::uuid AND EXISTS (
        SELECT 1 FROM public.hr_employees e
        JOIN public.hr_grants g ON g.client_id = e.client_id
        LEFT JOIN public.hr_employee_accounts a
            ON a.client_id = g.client_id AND a.user_id = g.user_id
        WHERE e.client_id = p_client AND e.id = p_employee AND e.is_active
          AND g.user_id = nullif(current_setting('app.hr_user_id',true),'')::uuid
          AND ((p_kind = 'directory' AND g.action = 'directory.read')
            OR (p_kind = 'training_read' AND g.action = 'training.read')
            OR (p_kind = 'training' AND g.action LIKE 'training.%'))
          AND (
            (g.scope = 'self' AND a.employee_id = e.id)
            OR (g.scope = 'assigned_manager' AND a.employee_id = e.manager_id
                AND (a.employee_id <> e.id OR EXISTS (
                    SELECT 1 FROM public.hr_client_owners o
                    WHERE o.client_id = g.client_id AND o.user_id = g.user_id)))
            OR g.scope = 'hr_operator'
            OR (g.scope = 'client_owner' AND e.manager_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM public.hr_client_owners o
                WHERE o.client_id = g.client_id AND o.user_id = g.user_id))
          )
    )
$$;
REVOKE ALL ON FUNCTION public.hr_resource_visible(uuid,uuid,text)
    FROM PUBLIC, csa_identity, csa_ro;
GRANT EXECUTE ON FUNCTION public.hr_resource_visible(uuid,uuid,text) TO csa_app;

CREATE FUNCTION public.hr_lock_authority(p_client uuid, p_user uuid)
RETURNS TABLE (employee_id uuid, client_owner boolean, grants jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE g public.hr_grants%ROWTYPE;
BEGIN
    IF p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid
       OR p_user IS DISTINCT FROM nullif(current_setting('app.hr_user_id',true),'')::uuid
    THEN RETURN; END IF;
    SELECT a.employee_id INTO employee_id FROM public.hr_employee_accounts a
        WHERE a.client_id = p_client AND a.user_id = p_user FOR SHARE;
    PERFORM 1 FROM public.hr_client_owners o
        WHERE o.client_id = p_client AND o.user_id = p_user FOR SHARE;
    client_owner := FOUND;
    grants := '[]'::jsonb;
    FOR g IN SELECT * FROM public.hr_grants h
        WHERE h.client_id = p_client AND h.user_id = p_user
        ORDER BY h.action,h.scope FOR SHARE LOOP
        grants := grants || jsonb_build_array(jsonb_build_object(
            'action',g.action,'scope',g.scope,'fields',g.fields));
    END LOOP;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION public.hr_lock_authority(uuid,uuid) FROM PUBLIC,csa_identity,csa_ro;
GRANT EXECUTE ON FUNCTION public.hr_lock_authority(uuid,uuid) TO csa_app;

CREATE FUNCTION public.hr_lock_employee(p_client uuid, p_employee uuid)
RETURNS TABLE (employee_id uuid, display_name text, job_title text, department text,
    work_email text, manager_id uuid, manager_active boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE e public.hr_employees%ROWTYPE;
BEGIN
    IF NOT (public.hr_resource_visible(p_client,p_employee,'directory')
        OR public.hr_resource_visible(p_client,p_employee,'training'))
    THEN RETURN; END IF;
    SELECT * INTO e FROM public.hr_employees h
        WHERE h.client_id = p_client AND h.id = p_employee AND h.is_active FOR SHARE;
    IF NOT FOUND THEN RETURN; END IF;
    SELECT h.is_active INTO manager_active FROM public.hr_employees h
        WHERE h.client_id = p_client AND h.id = e.manager_id FOR SHARE;
    employee_id := e.id; display_name := e.display_name; job_title := e.job_title;
    department := e.department; work_email := e.work_email; manager_id := e.manager_id;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION public.hr_lock_employee(uuid,uuid) FROM PUBLIC,csa_identity,csa_ro;
GRANT EXECUTE ON FUNCTION public.hr_lock_employee(uuid,uuid) TO csa_app;

DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['hr_employees','hr_employee_accounts','hr_client_owners',
        'hr_grants','hr_training_requests','hr_training_receipts','hr_training_audit'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,csa_app,csa_ro,csa_identity', t);
    END LOOP;
END $$;
CREATE POLICY hr_directory_scope ON public.hr_employees FOR SELECT TO csa_app USING (
    public.hr_resource_visible(client_id,id,'directory')
    OR public.hr_resource_visible(client_id,id,'training')
);
CREATE POLICY hr_account_self ON public.hr_employee_accounts FOR SELECT TO csa_app USING (
    client_id = nullif(current_setting('app.client_id',true),'')::uuid
    AND user_id = nullif(current_setting('app.hr_user_id',true),'')::uuid
);
CREATE POLICY hr_owner_self ON public.hr_client_owners FOR SELECT TO csa_app USING (
    client_id = nullif(current_setting('app.client_id',true),'')::uuid
    AND user_id = nullif(current_setting('app.hr_user_id',true),'')::uuid
);
CREATE POLICY hr_grant_self ON public.hr_grants FOR SELECT TO csa_app USING (
    client_id = nullif(current_setting('app.client_id',true),'')::uuid
    AND user_id = nullif(current_setting('app.hr_user_id',true),'')::uuid
);
CREATE POLICY hr_request_scope ON public.hr_training_requests TO csa_app USING (
    public.hr_resource_visible(client_id,employee_id,'training')
) WITH CHECK (public.hr_resource_visible(client_id,employee_id,'training'));
CREATE POLICY hr_receipt_self ON public.hr_training_receipts TO csa_app USING (
    client_id = nullif(current_setting('app.client_id',true),'')::uuid
    AND subject_id = nullif(current_setting('app.hr_user_id',true),'')::uuid
) WITH CHECK (
    client_id = nullif(current_setting('app.client_id',true),'')::uuid
    AND subject_id = nullif(current_setting('app.hr_user_id',true),'')::uuid
);
CREATE POLICY hr_audit_self ON public.hr_training_audit TO csa_app USING (
    client_id = nullif(current_setting('app.client_id',true),'')::uuid
    AND subject_id = nullif(current_setting('app.hr_user_id',true),'')::uuid
) WITH CHECK (
    client_id = nullif(current_setting('app.client_id',true),'')::uuid
    AND subject_id = nullif(current_setting('app.hr_user_id',true),'')::uuid
);
GRANT SELECT ON public.hr_employees, public.hr_employee_accounts,
    public.hr_client_owners, public.hr_grants TO csa_app;
GRANT SELECT, INSERT, UPDATE ON public.hr_training_requests TO csa_app;
GRANT SELECT, INSERT ON public.hr_training_receipts, public.hr_training_audit TO csa_app;

CREATE FUNCTION public.hr_guard_training_transition() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.version <> 0 THEN
            RAISE EXCEPTION 'hr_initial_state' USING ERRCODE = '23514';
        END IF;
    ELSIF (NEW.client_id,NEW.id,NEW.employee_id,NEW.created_by,NEW.title,NEW.learning_objective)
            IS DISTINCT FROM
          (OLD.client_id,OLD.id,OLD.employee_id,OLD.created_by,OLD.title,OLD.learning_objective)
        OR NEW.version::bigint <> OLD.version::bigint + 1
        OR NEW.updated_at < OLD.updated_at
        OR NOT ((OLD.status = 'draft' AND NEW.status IN ('submitted','withdrawn'))
             OR (OLD.status = 'submitted' AND NEW.status IN ('approved','rejected','withdrawn')))
    THEN RAISE EXCEPTION 'hr_invalid_transition' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.hr_guard_training_transition() FROM PUBLIC,csa_identity,csa_ro;
CREATE TRIGGER hr_training_transition BEFORE INSERT OR UPDATE ON public.hr_training_requests
    FOR EACH ROW EXECUTE FUNCTION public.hr_guard_training_transition();

CREATE FUNCTION public.hr_require_training_audit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.hr_training_audit a
        JOIN public.hr_training_receipts r USING (client_id,subject_id,request_key)
        WHERE a.client_id = NEW.client_id AND a.request_id = NEW.id
          AND a.version = NEW.version AND a.after_status = NEW.status
          AND a.occurred_at = NEW.updated_at AND r.request_id = NEW.id
          AND r.version = NEW.version AND r.status = NEW.status AND r.action = a.action
          AND r.updated_at = NEW.updated_at
          AND a.before_status IS NOT DISTINCT FROM
              CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.status END
          AND (TG_OP <> 'INSERT' OR a.subject_id = NEW.created_by)
          AND (NEW.decision_by IS NULL OR a.subject_id = NEW.decision_by)
    ) THEN RAISE EXCEPTION 'hr_audit_required' USING ERRCODE = '23514'; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.hr_require_training_audit() FROM PUBLIC,csa_app,csa_identity,csa_ro;
CREATE CONSTRAINT TRIGGER hr_training_audit_required
    AFTER INSERT OR UPDATE ON public.hr_training_requests
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION public.hr_require_training_audit();

CREATE FUNCTION public.hr_immutable_record() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN RAISE EXCEPTION 'hr_immutable_record' USING ERRCODE = '42501'; END $$;
REVOKE ALL ON FUNCTION public.hr_immutable_record() FROM PUBLIC,csa_identity,csa_ro;
CREATE TRIGGER hr_audit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
    ON public.hr_training_audit FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();
CREATE TRIGGER hr_receipt_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
    ON public.hr_training_receipts FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();
