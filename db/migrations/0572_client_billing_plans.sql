-- Client billing plans: each company's plan history (stage and bundle from a Kuala Lumpur date),
--   which decides its platform fee, markup and billed period per billing cycle. Written by
--   platform admins through csa.billing.plans.insert_plan, which enforces the plan-history rules
--   (spec "0552_client_billing_plans.sql", rules 1 to 7) and audits each insert in console_audit.
--   Append-only: a mistake is corrected by a newer row with the same effective_from.
-- Adds:
--   public.client_billing_plans (id, client_id, stage, bundle, effective_from date, reason,
--     created_by, created_at): client_id references clients; stage in (pilot, collaborator,
--     terminated), bundle in (individual, curated, complete); reason and created_by non-empty.
--     effective_from is a Kuala Lumpur date taking effect at its 00:00.
--   Index (client_id, effective_from, created_at) for one company's history in order.
--   Row security with the same tenant_isolation policy as ai_usage_events (0570), ENABLE only,
--     never FORCE.
--   public.client_billing_plans_append_only(): a BEFORE UPDATE OR DELETE trigger that raises, so
--     even the owner role cannot edit or remove history. TRUNCATE is left alone for test and
--     staging resets, as in 0571.
--   csa_app gets SELECT and INSERT only (explicit REVOKE first, as 0570 does: the default
--     privileges would otherwise grant UPDATE and DELETE); csa_ro gets SELECT; nothing to
--     PUBLIC, csa_identity or csa_hr_maintenance. No rows: existing companies stay unbilled until
--     an admin sets their first row.
-- No new roles and no FORCE ROW LEVEL SECURITY.
-- Pre-check: 0571 applied; clients exists.
-- Rollback:
--   DROP TABLE public.client_billing_plans;
--   DROP FUNCTION public.client_billing_plans_append_only();
--   The console_audit rows 'billing.plan_inserted' stay.
--   Finally delete the schema_migrations row for 0572, or the runner thinks it is still applied.

CREATE FUNCTION public.client_billing_plans_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% % refused: the table is append-only, insert a newer row with the same effective_from to correct one',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TABLE public.client_billing_plans (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    stage text NOT NULL CHECK (stage IN ('pilot', 'collaborator', 'terminated')),
    bundle text NOT NULL CHECK (bundle IN ('individual', 'curated', 'complete')),
    effective_from date NOT NULL,
    reason text NOT NULL CHECK (reason <> ''),
    created_by text NOT NULL CHECK (created_by <> ''),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX client_billing_plans_client_effective
    ON public.client_billing_plans (client_id, effective_from, created_at);

CREATE TRIGGER client_billing_plans_append_only BEFORE UPDATE OR DELETE
    ON public.client_billing_plans
    FOR EACH ROW EXECUTE FUNCTION public.client_billing_plans_append_only();

ALTER TABLE public.client_billing_plans ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.client_billing_plans
    USING (client_id = nullif(current_setting('app.client_id',true),'')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id',true),'')::uuid);
REVOKE ALL ON public.client_billing_plans
    FROM PUBLIC, csa_app, csa_ro, csa_identity, csa_hr_maintenance;
GRANT SELECT, INSERT ON public.client_billing_plans TO csa_app;
GRANT SELECT ON public.client_billing_plans TO csa_ro;
