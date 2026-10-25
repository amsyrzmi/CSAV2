-- Preconditions: 0440 applied; additive per-tenant service package records.
-- Rollback: DROP TABLE public.console_service_packages; no other data changes.
CREATE TABLE public.console_service_packages (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    package text NOT NULL,
    status text NOT NULL CHECK (status IN ('requested','active','suspended')),
    requested_by uuid,
    requested_at timestamptz,
    activated_by uuid,
    activated_at timestamptz,
    reference text CHECK (length(reference) <= 200),
    note text CHECK (length(note) <= 1000),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, package),
    CHECK (status <> 'active' OR activated_at IS NOT NULL)
);

ALTER TABLE public.console_service_packages ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.console_service_packages
    USING (client_id = nullif(current_setting('app.client_id',true),'')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id',true),'')::uuid);
REVOKE ALL ON public.console_service_packages FROM PUBLIC, csa_app, csa_ro;
GRANT SELECT, INSERT, UPDATE ON public.console_service_packages TO csa_app;

-- Member lists are duplicated from the code catalogue on purpose; a test compares them.
-- A package with no member rows for a client is never backfilled (count equality).
-- BACKFILL
INSERT INTO public.console_service_packages
    (client_id, package, status, requested_by, requested_at, activated_by, activated_at, reference, note)
SELECT client_id, package, 'active', NULL, NULL, NULL, now(), 'backfill-0450',
       'Backfilled from fully enabled capability switches'
FROM (
    SELECT c.client_id, p.package
    FROM (VALUES
        ('customer_support', 'kb_agent'), ('customer_support', 'ticket_agent'),
        ('customer_support', 'escalation_agent'), ('customer_support', 'order_approval'),
        ('orders_products', 'order_agent'), ('orders_products', 'invoice_agent'),
        ('subscriptions', 'subscription_agent'), ('bookings', 'booking_agent'),
        ('marketplaces', 'ecommerce_agent'), ('e_invoicing', 'lhdn_agent')
    ) AS p(package, member)
    JOIN public.capability_registry c ON c.name = p.member AND c.is_enabled
    GROUP BY c.client_id, p.package
    HAVING count(*) = (SELECT count(*) FROM (VALUES
        ('customer_support', 'kb_agent'), ('customer_support', 'ticket_agent'),
        ('customer_support', 'escalation_agent'), ('customer_support', 'order_approval'),
        ('orders_products', 'order_agent'), ('orders_products', 'invoice_agent'),
        ('subscriptions', 'subscription_agent'), ('bookings', 'booking_agent'),
        ('marketplaces', 'ecommerce_agent'), ('e_invoicing', 'lhdn_agent')
    ) AS t(package, member) WHERE t.package = p.package)
    UNION
    SELECT client_id, 'hr' FROM public.hr_agent_settings WHERE enabled
    UNION
    SELECT client_id, 'staff_email' FROM public.email_settings WHERE enabled
) AS eligible(client_id, package)
ON CONFLICT DO NOTHING;
