-- Per-turn decision provider log (LG06): which provider decided, each provider's typed
-- answers, latency and disagreements. Answers are codes and probabilities, never customer text.

CREATE TABLE IF NOT EXISTS public.decision_log (
    id bigserial PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients (id),
    trace_id uuid NOT NULL,
    node text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('shadow', 'cascade', 'jev')),
    provider text NOT NULL CHECK (provider IN ('jev', 'llm')),
    model_version text,
    latency_ms integer NOT NULL CHECK (latency_ms >= 0),
    answers jsonb NOT NULL,
    final jsonb NOT NULL,
    disagree text[] NOT NULL DEFAULT '{}',
    fallback_reason text,
    human_outcome text,
    created_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_decision_log_client ON public.decision_log (client_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_decision_log_trace ON public.decision_log (client_id, trace_id);

ALTER TABLE public.decision_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.decision_log
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
GRANT SELECT, INSERT ON public.decision_log TO csa_app;
GRANT USAGE ON SEQUENCE public.decision_log_id_seq TO csa_app;
GRANT SELECT ON public.decision_log TO csa_ro;
