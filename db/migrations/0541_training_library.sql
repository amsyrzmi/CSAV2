-- The training library: one row per training PDF a company keeps, and one read-only check that says
--   whether the session's user is the company's HR owner.
-- Adds:
--   public.training_items: client_id references clients; row security on it with the same
--     tenant_isolation policy as console_service_packages (0450), ENABLE only, never FORCE.
--     SELECT, INSERT, UPDATE, DELETE to csa_app; nothing to PUBLIC, csa_ro or csa_identity. Rows are
--     removed for real; the stored file is deleted by the app after the row commits. request_key and
--     fingerprint are the upload's idempotency key, unique per company, as is storage_name.
--   public.training_is_hr_owner(): no arguments. Reads app.client_id and app.hr_user_id from the
--     session, as hr_owner_read (0522) does, and answers true only when both are set, that pair is the
--     company's row in hr_client_owners, and the user and their membership of that company are
--     enabled and the role is not read_only. It cannot be asked about another user or company.
--     SECURITY DEFINER, pinned to search_path, EXECUTE to csa_app only. The only place the training
--     library touches an HR table.
-- No new roles and no FORCE ROW LEVEL SECURITY.
-- Pre-check: 0540 applied; clients, console_users, console_memberships (0270) and hr_client_owners
--   (0380) exist.
-- Rollback:
--   DROP FUNCTION public.training_is_hr_owner();
--   DROP TABLE public.training_items;
--   (Stored training files stay in the training store with no row; the store's sweep removes them.
--   The console_audit rows stay.) Finally delete the schema_migrations row for 0541, or the runner
--   thinks it is still applied.

CREATE TABLE public.training_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    kind text NOT NULL CHECK (kind IN ('pdf')),
    title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
    description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 1000),
    category text NOT NULL DEFAULT '' CHECK (char_length(category) <= 60),
    view_mode text NOT NULL CHECK (view_mode IN ('slides','document')),
    storage_name text NOT NULL,
    original_name text NOT NULL,
    media_type text NOT NULL,
    byte_size bigint NOT NULL CHECK (byte_size > 0),
    sha256 text NOT NULL,
    page_count int NOT NULL CHECK (page_count > 0),
    summary text,
    summary_truncated boolean NOT NULL DEFAULT false,
    summary_model text,
    summarised_at timestamptz,
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    request_key uuid NOT NULL,
    fingerprint text NOT NULL,
    UNIQUE (client_id, storage_name),
    UNIQUE (client_id, request_key)
);

ALTER TABLE public.training_items ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.training_items
    USING (client_id = nullif(current_setting('app.client_id',true),'')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id',true),'')::uuid);
REVOKE ALL ON public.training_items FROM PUBLIC, csa_ro, csa_identity;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.training_items TO csa_app;

-- Whether the session's user is this company's HR owner and can act. Both settings must be set:
-- an unset or empty one is NULL, no row matches, and the answer is false.
CREATE FUNCTION public.training_is_hr_owner()
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT EXISTS(
  SELECT 1 FROM public.hr_client_owners o
  JOIN public.console_users u ON u.id=o.user_id
  JOIN public.console_memberships m ON m.user_id=o.user_id AND m.client_id=o.client_id
  WHERE o.client_id=nullif(current_setting('app.client_id',true),'')::uuid
   AND o.user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid
   AND u.is_enabled AND m.is_enabled AND m.role<>'read_only')
$$;
REVOKE ALL ON FUNCTION public.training_is_hr_owner() FROM PUBLIC, csa_ro, csa_identity, csa_hr_maintenance;
GRANT EXECUTE ON FUNCTION public.training_is_hr_owner() TO csa_app;
