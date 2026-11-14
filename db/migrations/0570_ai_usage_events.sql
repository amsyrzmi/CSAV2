-- The AI usage ledger: one row per model call made on a company's behalf, so credit use can be
--   metered and billed. Written by the app after each call; never edited or removed.
-- Adds:
--   public.ai_usage_events: client_id references clients; row security on it with the same
--     tenant_isolation policy as training_items (0541), ENABLE only, never FORCE. INSERT and SELECT
--     to csa_app, SELECT to csa_ro; no UPDATE or DELETE for anyone, and nothing to PUBLIC or
--     csa_identity. job is a closed set; token counts are never negative. The 30-day purges do not
--     touch this table: it backs billing.
--   Indexes (client_id, occurred_at) for a company's usage over a period and (client_id, trace_id)
--     for the calls one turn made.
-- No new roles and no FORCE ROW LEVEL SECURITY.
-- Pre-check: 0560 applied; clients exists.
-- Rollback:
--   DROP TABLE public.ai_usage_events;
--   Finally delete the schema_migrations row for 0570, or the runner thinks it is still applied.

CREATE TABLE public.ai_usage_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    trace_id uuid NOT NULL,
    conversation_id uuid,
    job text NOT NULL CHECK (job IN (
        'customer_turn', 'workspace_assistant', 'hr_assistant', 'email_analysis',
        'email_reply', 'email_compose', 'training_summary')),
    provider text NOT NULL,
    model text NOT NULL,
    prompt_tokens int NOT NULL CHECK (prompt_tokens >= 0),
    completion_tokens int NOT NULL CHECK (completion_tokens >= 0),
    schema_name text NOT NULL
);

CREATE INDEX ai_usage_events_client_occurred ON public.ai_usage_events (client_id, occurred_at);
CREATE INDEX ai_usage_events_client_trace ON public.ai_usage_events (client_id, trace_id);

ALTER TABLE public.ai_usage_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.ai_usage_events
    USING (client_id = nullif(current_setting('app.client_id',true),'')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id',true),'')::uuid);
REVOKE ALL ON public.ai_usage_events FROM PUBLIC, csa_app, csa_ro, csa_identity, csa_hr_maintenance;
GRANT SELECT, INSERT ON public.ai_usage_events TO csa_app;
GRANT SELECT ON public.ai_usage_events TO csa_ro;
