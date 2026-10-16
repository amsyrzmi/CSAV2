-- Durable upload metadata and deletion fences; no imported legacy files.
CREATE TABLE public.kb_upload_batches (
    id uuid PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    idempotency_key uuid NOT NULL,
    fingerprint text NOT NULL,
    job_id uuid NOT NULL REFERENCES public.kb_ingest_jobs(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, idempotency_key)
);
CREATE TABLE public.kb_upload_files (
    id uuid PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    batch_id uuid NOT NULL REFERENCES public.kb_upload_batches(id),
    document_id uuid REFERENCES public.kb_documents(id) ON DELETE SET NULL,
    storage_name text NOT NULL CHECK (storage_name ~ '^[a-f0-9-]{36}\.(pdf|docx|csv)$'),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 255),
    media_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes BETWEEN 1 AND 10485760),
    content_hash text NOT NULL,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, storage_name)
);
CREATE INDEX kb_upload_files_document ON public.kb_upload_files(client_id, document_id);
CREATE TABLE public.kb_deletions (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    content_hash text NOT NULL,
    deleted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, content_hash)
);
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['kb_upload_batches','kb_upload_files','kb_deletions'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON public.%I USING '
            || '(client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid) '
            || 'WITH CHECK '
            || '(client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid)', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO csa_app', t);
        EXECUTE format('GRANT SELECT ON public.%I TO csa_ro', t);
    END LOOP;
END $$;
