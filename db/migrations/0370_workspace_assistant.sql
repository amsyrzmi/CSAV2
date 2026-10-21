-- A1: owner-private, authenticated internal workspace threads and read receipts.
-- No customer contact, conversation, channel credential or model queue is reused.
-- Rollback: stop workspace API traffic, drop both workspace tables, then drop the
-- session-owner constraint. Existing business and customer-chat records are unchanged.
ALTER TABLE public.console_sessions
    ADD CONSTRAINT console_sessions_id_user_unique UNIQUE (id, user_id);

CREATE TABLE public.console_workspace_threads (
    sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    id uuid PRIMARY KEY DEFAULT public.uuid_generate_v4(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    owner_user_id uuid NOT NULL REFERENCES public.console_users(id),
    create_request_key uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, owner_user_id, create_request_key),
    UNIQUE (client_id, id, owner_user_id)
);

CREATE TABLE public.console_workspace_turns (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    thread_id uuid NOT NULL,
    owner_user_id uuid NOT NULL REFERENCES public.console_users(id),
    session_id uuid NOT NULL,
    request_key uuid NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    body text NOT NULL CHECK (length(body) BETWEEN 1 AND 8000),
    action text NOT NULL CHECK (action IN ('page_guidance', 'local_order_read')),
    page_key text NOT NULL CHECK (
        page_key IN ('workspace_home', 'business_orders', 'business_order')
    ),
    resource_id text,
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (action = 'page_guidance' AND resource_id IS NULL)
        OR
        (action = 'local_order_read' AND page_key = 'business_order'
            AND resource_id IS NOT NULL AND length(resource_id) BETWEEN 1 AND 100)
    ),
    UNIQUE (thread_id, request_key),
    FOREIGN KEY (client_id, thread_id, owner_user_id)
        REFERENCES public.console_workspace_threads(client_id, id, owner_user_id),
    FOREIGN KEY (session_id, owner_user_id)
        REFERENCES public.console_sessions(id, user_id)
);

CREATE INDEX console_workspace_threads_owner
    ON public.console_workspace_threads (client_id, owner_user_id, sequence DESC);
CREATE INDEX console_workspace_turns_history
    ON public.console_workspace_turns (client_id, thread_id, id DESC);

ALTER TABLE public.console_workspace_threads ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.console_workspace_threads
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
ALTER TABLE public.console_workspace_turns ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.console_workspace_turns
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);

REVOKE ALL ON public.console_workspace_threads, public.console_workspace_turns
    FROM PUBLIC, csa_app, csa_ro;
GRANT SELECT, INSERT ON public.console_workspace_threads,
    public.console_workspace_turns TO csa_app;
GRANT USAGE, SELECT ON SEQUENCE public.console_workspace_threads_sequence_seq,
    public.console_workspace_turns_id_seq TO csa_app;
