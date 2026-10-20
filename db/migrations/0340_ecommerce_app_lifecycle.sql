-- Global non-secret app gate. Existing grants retain reserved revision 1;
-- no registry or authorization is inferred/backfilled.
CREATE TABLE public.ecommerce_apps (
    app_id text PRIMARY KEY CHECK (length(app_id) BETWEEN 1 AND 100),
    revision bigint NOT NULL CHECK (revision >= 1),
    state text NOT NULL CHECK (state IN ('active', 'disabled'))
);
CREATE TABLE public.ecommerce_app_events (
    app_id text NOT NULL REFERENCES public.ecommerce_apps(app_id),
    revision bigint NOT NULL,
    state text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (app_id, revision)
);

CREATE FUNCTION public.guard_ecommerce_app() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.revision = 1 AND NEW.state = 'disabled' THEN
            RETURN NEW;
        END IF;
    ELSIF TG_OP = 'UPDATE' THEN
        IF NEW.app_id = OLD.app_id AND NEW.revision = OLD.revision + 1
           AND NEW.state <> OLD.state THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'invalid ecommerce app transition' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER ecommerce_app_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.ecommerce_apps FOR EACH ROW EXECUTE FUNCTION public.guard_ecommerce_app();

CREATE FUNCTION public.audit_ecommerce_app() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.ecommerce_app_events (app_id, revision, state)
    VALUES (NEW.app_id, NEW.revision, NEW.state);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.audit_ecommerce_app() FROM PUBLIC, csa_app, csa_ro;
CREATE TRIGGER ecommerce_app_audit AFTER INSERT OR UPDATE ON public.ecommerce_apps
    FOR EACH ROW EXECUTE FUNCTION public.audit_ecommerce_app();

-- Runtime needs a shared fence, not UPDATE privileges or app-wide invalidation DML.
CREATE FUNCTION public.lock_ecommerce_app(wanted_app text, wanted_revision bigint)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM 1 FROM public.ecommerce_apps
    WHERE app_id = wanted_app AND revision = wanted_revision AND state = 'active'
    FOR SHARE;
    RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION public.lock_ecommerce_app(text, bigint)
    FROM PUBLIC, csa_app, csa_ro;
GRANT EXECUTE ON FUNCTION public.lock_ecommerce_app(text, bigint) TO csa_app;
REVOKE ALL ON public.ecommerce_apps, public.ecommerce_app_events
    FROM PUBLIC, csa_app, csa_ro;
GRANT SELECT ON public.ecommerce_apps, public.ecommerce_app_events TO csa_app, csa_ro;

ALTER TABLE public.ecommerce_grants
    ADD COLUMN app_revision bigint NOT NULL DEFAULT 1 CHECK (app_revision >= 1);
