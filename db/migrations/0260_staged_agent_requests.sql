-- Provider-neutral requests only; deliberately no execution worker or approval transition.
-- Pre-check: 0250 applied, csa_app/csa_ro and contact_identity_tenant_check exist.
-- Rollback: disable both capabilities and stop callers; preserve prepared records for audit.
CREATE TABLE public.agent_preparation_requests (
    id uuid PRIMARY KEY DEFAULT public.uuid_generate_v4(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    contact_id uuid NOT NULL REFERENCES public.contacts(id),
    agent text NOT NULL CHECK (agent IN (
        'booking_agent', 'subscription_agent', 'email_agent',
        'web_research_agent', 'invoice_write_agent'
    )),
    action text NOT NULL,
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'prepared' CHECK (status = 'prepared'),
    trace_id uuid NOT NULL,
    requested_by text NOT NULL CHECK (length(requested_by) BETWEEN 1 AND 128),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, contact_id, agent, idempotency_key)
);
ALTER TABLE public.agent_preparation_requests ENABLE ROW LEVEL SECURITY;
CREATE TRIGGER trg_preparation_contact_tenant
    BEFORE INSERT ON public.agent_preparation_requests
    FOR EACH ROW EXECUTE FUNCTION public.contact_identity_tenant_check();
CREATE POLICY tenant_isolation ON public.agent_preparation_requests
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
REVOKE UPDATE, DELETE ON public.agent_preparation_requests FROM csa_app;
GRANT SELECT, INSERT ON public.agent_preparation_requests TO csa_app;
GRANT SELECT ON public.agent_preparation_requests TO csa_ro;

CREATE FUNCTION public.seed_preparation_capabilities(p_client_id uuid) RETURNS void
LANGUAGE sql AS $$
INSERT INTO public.capability_registry (
    client_id, name, display_name, description, pattern, tier, version,
    risk_class, input_schema, is_enabled, client_enabled, workflow_id
)
SELECT p_client_id, a.name, a.label, 'Provider-neutral local reads and unexecuted requests',
       'python', 'standard', '1.0', 'low', '{}'::jsonb, false, false, 'python:' || a.name
FROM (
    VALUES ('booking_agent', 'Booking preparation'),
           ('subscription_agent', 'Subscription preparation')
) AS a(name, label)
ON CONFLICT (client_id, name) DO NOTHING;
$$;
SELECT public.seed_preparation_capabilities(id) FROM public.clients;
