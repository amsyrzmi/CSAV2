-- Precondition: 0380 HR and console identity. Staff-only agents, disabled initially.
-- Rollback: disable switches/mounts; preserve confirmations and audit receipts.
CREATE TABLE public.hr_agent_settings (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    name text NOT NULL CHECK (name IN ('hr_directory','hr_training','hr_recruitment','hr_attendance','hr_payroll')),
    enabled boolean NOT NULL DEFAULT false,
    PRIMARY KEY (client_id,name)
);
INSERT INTO public.hr_agent_settings(client_id,name)
SELECT c.id,n FROM public.clients c CROSS JOIN unnest(ARRAY['hr_directory','hr_training','hr_recruitment','hr_attendance','hr_payroll']) n;
CREATE FUNCTION public.hr_new_client_agents() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    INSERT INTO public.hr_agent_settings(client_id,name)
    SELECT NEW.id,n FROM unnest(ARRAY['hr_directory','hr_training','hr_recruitment','hr_attendance','hr_payroll']) n;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.hr_new_client_agents() FROM PUBLIC,csa_app,csa_ro,csa_identity;
CREATE TRIGGER hr_client_agent_defaults AFTER INSERT ON public.clients
    FOR EACH ROW EXECUTE FUNCTION public.hr_new_client_agents();
ALTER TABLE public.hr_agent_settings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.hr_agent_settings FROM PUBLIC,csa_app,csa_ro,csa_identity;
GRANT SELECT,UPDATE ON public.hr_agent_settings TO csa_app;
CREATE POLICY hr_agent_read ON public.hr_agent_settings FOR SELECT TO csa_app USING (
    client_id=nullif(current_setting('app.client_id',true),'')::uuid
    AND nullif(current_setting('app.hr_user_id',true),'') IS NOT NULL
);
CREATE POLICY hr_agent_owner_update ON public.hr_agent_settings FOR UPDATE TO csa_app USING (
    client_id=nullif(current_setting('app.client_id',true),'')::uuid AND EXISTS (
        SELECT 1 FROM public.hr_client_owners o WHERE o.client_id=hr_agent_settings.client_id
        AND o.user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid)
) WITH CHECK (client_id=nullif(current_setting('app.client_id',true),'')::uuid);

CREATE FUNCTION public.hr_lock_agent_switch(p_client uuid,p_name text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE allowed boolean;
BEGIN
    IF p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid
        OR nullif(current_setting('app.hr_user_id',true),'') IS NULL THEN RETURN false; END IF;
    SELECT enabled INTO allowed FROM public.hr_agent_settings
        WHERE client_id=p_client AND name=p_name FOR SHARE;
    RETURN coalesce(allowed,false);
END $$;
REVOKE ALL ON FUNCTION public.hr_lock_agent_switch(uuid,text) FROM PUBLIC,csa_ro,csa_identity;
GRANT EXECUTE ON FUNCTION public.hr_lock_agent_switch(uuid,text) TO csa_app;

CREATE TABLE public.hr_agent_confirmations (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    id uuid NOT NULL,
    subject_id uuid NOT NULL,
    session_id uuid NOT NULL REFERENCES public.console_sessions(id),
    request_id uuid NOT NULL,
    request_key uuid NOT NULL,
    command jsonb NOT NULL CHECK (jsonb_typeof(command)='object'),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    PRIMARY KEY (client_id,id),
    UNIQUE (client_id,subject_id,request_key),
    FOREIGN KEY (subject_id,client_id) REFERENCES public.console_memberships(user_id,client_id),
    FOREIGN KEY (client_id,request_id) REFERENCES public.hr_training_requests(client_id,id),
    CHECK (expires_at>created_at AND expires_at<=created_at+interval '15 minutes')
);
ALTER TABLE public.hr_agent_confirmations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.hr_agent_confirmations FROM PUBLIC,csa_app,csa_ro,csa_identity;
GRANT SELECT,INSERT,UPDATE ON public.hr_agent_confirmations TO csa_app;
CREATE POLICY hr_confirmation_owner ON public.hr_agent_confirmations TO csa_app USING (
    client_id=nullif(current_setting('app.client_id',true),'')::uuid
    AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid
) WITH CHECK (
    client_id=nullif(current_setting('app.client_id',true),'')::uuid
    AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid
);
CREATE FUNCTION public.hr_confirmation_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF (NEW.client_id,NEW.id,NEW.subject_id,NEW.session_id,NEW.request_id,NEW.request_key,
        NEW.command,NEW.fingerprint,NEW.created_at,NEW.expires_at) IS DISTINCT FROM
       (OLD.client_id,OLD.id,OLD.subject_id,OLD.session_id,OLD.request_id,OLD.request_key,
        OLD.command,OLD.fingerprint,OLD.created_at,OLD.expires_at)
       OR OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL THEN
        RAISE EXCEPTION 'hr_confirmation_immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.hr_confirmation_immutable() FROM PUBLIC,csa_identity,csa_ro;
CREATE TRIGGER hr_confirmation_guard BEFORE UPDATE ON public.hr_agent_confirmations
    FOR EACH ROW EXECUTE FUNCTION public.hr_confirmation_immutable();
