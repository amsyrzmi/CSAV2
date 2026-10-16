-- Private console sandbox conversations. No customer identity or credentials imported.
CREATE TABLE public.console_chat_participants (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    user_id uuid NOT NULL REFERENCES public.console_users(id),
    contact_id uuid NOT NULL UNIQUE REFERENCES public.contacts(id),
    endpoint_id uuid NOT NULL UNIQUE REFERENCES public.webhook_endpoints(id),
    PRIMARY KEY (client_id, user_id),
    UNIQUE (client_id, user_id, contact_id, endpoint_id)
);
CREATE TABLE public.console_chat_threads (
    sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    id uuid PRIMARY KEY REFERENCES public.conversations(id),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    user_id uuid NOT NULL REFERENCES public.console_users(id),
    contact_id uuid NOT NULL REFERENCES public.contacts(id),
    endpoint_id uuid NOT NULL REFERENCES public.webhook_endpoints(id),
    request_key uuid NOT NULL,
    archived_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, user_id, request_key),
    FOREIGN KEY (client_id, user_id, contact_id, endpoint_id)
        REFERENCES public.console_chat_participants(client_id, user_id, contact_id, endpoint_id)
);
CREATE TABLE public.console_chat_turns (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    conversation_id uuid NOT NULL REFERENCES public.console_chat_threads(id),
    request_key uuid NOT NULL,
    fingerprint text NOT NULL,
    trace_id uuid NOT NULL UNIQUE,
    body text NOT NULL CHECK (length(body) BETWEEN 1 AND 8000),
    held boolean NOT NULL DEFAULT false,
    hold_reason text,
    held_draft text,
    failed boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, request_key)
);
CREATE INDEX console_chat_threads_owner ON public.console_chat_threads
    (client_id, user_id, sequence DESC);
CREATE INDEX console_chat_turns_history ON public.console_chat_turns
    (client_id, conversation_id, id DESC);
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'console_chat_participants','console_chat_threads','console_chat_turns'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON public.%I USING '
            || '(client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid) '
            || 'WITH CHECK '
            || '(client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid)', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO csa_app', t);
        EXECUTE format('GRANT SELECT ON public.%I TO csa_ro', t);
    END LOOP;
END $$;
GRANT USAGE, SELECT ON SEQUENCE public.console_chat_threads_sequence_seq,
    public.console_chat_turns_id_seq TO csa_app;
