-- Preconditions: 0410 applied. Additive; legacy owner access is preserved.
-- Rollback: disable VOICE_STT_ENDPOINT first; drop this new table only after retention review.
ALTER TABLE public.console_chat_threads ADD CONSTRAINT console_chat_threads_voice_owner
    UNIQUE (id, client_id, user_id);
CREATE TABLE public.console_voice_jobs (
    id uuid PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    user_id uuid NOT NULL REFERENCES public.console_users(id),
    conversation_id uuid NOT NULL,
    request_key uuid NOT NULL,
    fingerprint text NOT NULL,
    status text NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing','completed','failed','timeout','expired')),
    transcript text CHECK (length(transcript) <= 4000),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, user_id, conversation_id, request_key),
    FOREIGN KEY (conversation_id, client_id, user_id)
        REFERENCES public.console_chat_threads(id, client_id, user_id)
);
ALTER TABLE public.console_voice_jobs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.console_voice_jobs
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE ON public.console_voice_jobs TO csa_app;
GRANT SELECT ON public.console_voice_jobs TO csa_ro;
