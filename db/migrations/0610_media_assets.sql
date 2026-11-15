-- Pictures, part 1: the shared picture store.
-- Adds, both with client_id (references clients), row security with the same tenant_isolation policy
--   as training_items (0541), ENABLE only, never FORCE; SELECT, INSERT, UPDATE, DELETE to csa_app;
--   nothing to PUBLIC or csa_identity:
--   public.media_assets: one row per picture, the metadata only (id, kind, subject_key, media_type,
--     byte_size, width, height, sha256, source, created_by, created_at, updated_at).
--     kind is payment_qr, plan, product or training_thumbnail; subject_key is the payment method id,
--     the plan code, the product id or the training item id, as text. UNIQUE (client_id, kind,
--     subject_key): one picture per subject; replacing it keeps the row id and changes sha256, which
--     is the version. UNIQUE (client_id, id) is the target of the bytes table's foreign key.
--     csa_ro gets SELECT on this table only (the metadata is not sensitive).
--   public.media_asset_data: the bytes (PK asset_id), in their own table so a metadata listing never
--     reads them. Its composite foreign key (client_id, asset_id) to media_assets(client_id, id) ON
--     DELETE CASCADE means a bytes row cannot pair with another company's asset, and the bytes go
--     with their asset. csa_ro has no access to this table.
-- No new roles and no FORCE ROW LEVEL SECURITY. Safe to run twice: IF NOT EXISTS, policies dropped
--   and made again, grants and revokes repeat harmlessly.
-- Pre-check: nothing beyond the baseline (clients, the uuid-ossp extension, the csa_app and csa_ro
--   roles). The tables are new and empty; nothing is rewritten.
-- Rollback (after the app no longer reads them; as the database owner):
--   DROP TABLE public.media_asset_data, public.media_assets; (every uploaded picture is lost)
--   Finally delete the schema_migrations row for 0610, or the runner thinks it is still applied.

CREATE TABLE IF NOT EXISTS public.media_assets (
    id uuid PRIMARY KEY DEFAULT public.uuid_generate_v4(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    kind text NOT NULL
        CHECK (kind IN ('payment_qr', 'plan', 'product', 'training_thumbnail')),
    subject_key text NOT NULL CHECK (char_length(subject_key) BETWEEN 1 AND 128),
    media_type text NOT NULL CHECK (media_type IN ('image/jpeg', 'image/png')),
    byte_size integer NOT NULL CHECK (byte_size BETWEEN 1 AND 2097152),
    width integer NOT NULL CHECK (width > 0),
    height integer NOT NULL CHECK (height > 0),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    source text NOT NULL CHECK (source IN ('upload', 'generated')),
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, kind, subject_key),
    UNIQUE (client_id, id)
);

CREATE TABLE IF NOT EXISTS public.media_asset_data (
    asset_id uuid PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    data bytea NOT NULL,
    FOREIGN KEY (client_id, asset_id)
        REFERENCES public.media_assets(client_id, id) ON DELETE CASCADE
);

DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['media_assets', 'media_asset_data'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON public.%I'
      ' USING (client_id = nullif(current_setting(''app.client_id'',true),'''')::uuid)'
      ' WITH CHECK (client_id = nullif(current_setting(''app.client_id'',true),'''')::uuid)', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, csa_ro, csa_identity', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO csa_app', t);
  END LOOP;
END $$;

GRANT SELECT ON public.media_assets TO csa_ro;
