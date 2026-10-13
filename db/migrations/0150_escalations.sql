-- 0150_escalations.sql — durable, idempotent escalation records (LG04).
-- One row per turn (trace_id) so fail_safe/handoff replays never duplicate.

CREATE TABLE IF NOT EXISTS public.escalations (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients (id),
    trace_id uuid NOT NULL,
    conversation_id uuid,
    contact_id uuid,
    reason text NOT NULL,
    priority text NOT NULL DEFAULT 'normal',
    status text NOT NULL DEFAULT 'open',
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT escalations_priority_check CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    CONSTRAINT escalations_status_check CHECK (status IN ('open', 'claimed', 'resolved')),
    CONSTRAINT escalations_trace_unique UNIQUE (client_id, trace_id)
);

ALTER TABLE public.escalations ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.escalations
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.escalations TO csa_app;
GRANT SELECT ON public.escalations TO csa_ro;
