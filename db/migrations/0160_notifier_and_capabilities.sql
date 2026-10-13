-- 0160_notifier_and_capabilities.sql — staff notification targets/outbox and per-tenant
-- capability registry rows for the LG05 spokes (LG05, LG11 M08).

CREATE TABLE IF NOT EXISTS public.staff_notification_targets (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients (id),
    channel text NOT NULL CHECK (channel IN ('telegram', 'email', 'whatsapp', 'webhook')),
    address text NOT NULL,
    events text[] NOT NULL DEFAULT '{escalation,approval_needed,order_submitted,dlq,circuit_opened}',
    min_priority text NOT NULL DEFAULT 'normal'
        CHECK (min_priority IN ('low', 'normal', 'high', 'urgent')),
    quiet_hours jsonb,
    is_enabled boolean NOT NULL DEFAULT true,
    verified_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    UNIQUE (client_id, channel, address)
);

CREATE TABLE IF NOT EXISTS public.staff_notifications (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients (id),
    target_id uuid NOT NULL REFERENCES public.staff_notification_targets (id),
    event text NOT NULL,
    priority text NOT NULL DEFAULT 'normal',
    payload jsonb NOT NULL,
    idempotency_key text NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivering', 'delivered', 'dead')),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamp with time zone NOT NULL DEFAULT now(),
    last_error text,
    delivered_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    UNIQUE (client_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS staff_notifications_due
    ON public.staff_notifications (next_attempt_at) WHERE status = 'pending';

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['staff_notification_targets', 'staff_notifications'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON public.%I '
            'USING (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid) '
            'WITH CHECK (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid)',
            t
        );
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO csa_app', t);
        EXECUTE format('GRANT SELECT ON public.%I TO csa_ro', t);
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION public.seed_core_capabilities(p_client_id uuid) RETURNS void
    LANGUAGE sql
    AS $$
    INSERT INTO public.capability_registry
        (client_id, name, display_name, description, pattern, risk_class, workflow_id,
         input_schema, timeout_ms, max_retries, capability_kind)
    VALUES
        (p_client_id, 'order_agent', 'Orders', 'Order status, catalogue and drafts',
         'python', 'medium', 'python:order_agent', '{}'::jsonb, 15000, 1, 'dispatched'),
        (p_client_id, 'escalation_agent', 'Escalation', 'Hand a conversation to a human',
         'python', 'low', 'python:escalation_agent', '{}'::jsonb, 10000, 1, 'dispatched'),
        (p_client_id, 'ticket_agent', 'Tickets', 'Support tickets',
         'python', 'low', 'python:ticket_agent', '{}'::jsonb, 10000, 1, 'dispatched'),
        (p_client_id, 'order_approval', 'Order approval', 'Staff approve or reject orders',
         'python', 'high', 'python:order_approval', '{}'::jsonb, 15000, 0, 'dispatched')
    ON CONFLICT (client_id, name) DO NOTHING;
$$;

-- Onboarding and seeds call seed_core_capabilities(client_id); no trigger, so tests and
-- tenants that manage their own registry rows are unaffected.
SELECT public.seed_core_capabilities(id) FROM public.clients;
