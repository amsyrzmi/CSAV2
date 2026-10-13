-- Append-only per-turn timeline for the web console: one row per graph step or agent run.
-- Summaries are redacted by construction (codes, counts, names; never customer text).

CREATE TABLE IF NOT EXISTS public.agent_events (
    id bigserial PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients (id),
    trace_id uuid NOT NULL,
    conversation_id uuid,
    node text NOT NULL,
    agent text,
    status text NOT NULL CHECK (status IN ('ok', 'error')),
    duration_ms integer NOT NULL CHECK (duration_ms >= 0),
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_events_trace ON public.agent_events (client_id, trace_id, id);
CREATE INDEX IF NOT EXISTS idx_agent_events_client_id ON public.agent_events (client_id, id DESC);

ALTER TABLE public.agent_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.agent_events
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
GRANT SELECT, INSERT ON public.agent_events TO csa_app;
GRANT USAGE ON SEQUENCE public.agent_events_id_seq TO csa_app;
GRANT SELECT ON public.agent_events TO csa_ro;
