-- Subscriptions console tab: status and interval checks, optimistic version, decision ledger,
-- proposal kind, supporting indexes.
-- Pre-check: 0460 applied (console_proposals); validates existing rows before adding CHECKs,
--   so legacy subscriptions.status values outside (active, paused, cancelled) become 'cancelled'
--   and legacy plans.billing_interval values are normalised to 'month' / 'year' first.
-- Rollback: drop the new table and constraints (DROP TABLE public.subscription_request_decisions;
--   drop subscriptions_status_chk, plans_billing_interval_chk and the new indexes; restore
--   console_proposals_kind_check without 'subscription_action' once no such rows remain);
--   data columns (subscriptions.version, subscription_events.proposal_id) stay.

DO $$
BEGIN
  IF NOT has_table_privilege('csa_app', 'public.subscriptions', 'UPDATE')
     OR NOT has_table_privilege('csa_app', 'public.plans', 'UPDATE')
     OR NOT has_table_privilege('csa_app', 'public.subscription_events', 'INSERT') THEN
    RAISE EXCEPTION '0480: csa_app lacks the DML grants 0100 should have given on plans, subscriptions, subscription_events';
  END IF;
END $$;

ALTER TABLE public.subscriptions ADD COLUMN version integer NOT NULL DEFAULT 1;

DO $$
DECLARE
  legacy_status integer;
  legacy_interval integer;
BEGIN
  UPDATE public.subscriptions SET status = 'cancelled'
   WHERE status NOT IN ('active', 'paused', 'cancelled');
  GET DIAGNOSTICS legacy_status = ROW_COUNT;
  RAISE NOTICE '0480: % subscription(s) with a legacy status set to cancelled', legacy_status;

  UPDATE public.plans
     SET billing_interval = CASE
           WHEN lower(billing_interval) IN ('year', 'yearly', 'annual', 'annually') THEN 'year'
           ELSE 'month' END
   WHERE billing_interval NOT IN ('month', 'year');
  GET DIAGNOSTICS legacy_interval = ROW_COUNT;
  RAISE NOTICE '0480: % plan(s) with a legacy billing_interval normalised', legacy_interval;
END $$;

ALTER TABLE public.subscriptions
    ADD CONSTRAINT subscriptions_status_chk CHECK (status IN ('active', 'paused', 'cancelled')) NOT VALID;
ALTER TABLE public.subscriptions VALIDATE CONSTRAINT subscriptions_status_chk;
ALTER TABLE public.plans
    ADD CONSTRAINT plans_billing_interval_chk CHECK (billing_interval IN ('month', 'year')) NOT VALID;
ALTER TABLE public.plans VALIDATE CONSTRAINT plans_billing_interval_chk;

ALTER TABLE public.subscription_events ADD COLUMN proposal_id uuid;
CREATE UNIQUE INDEX subscription_events_proposal_key
    ON public.subscription_events (proposal_id) WHERE proposal_id IS NOT NULL;

-- Final decisions on staged subscription requests: insert-only, one decision per request.
CREATE TABLE public.subscription_request_decisions (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    request_id uuid PRIMARY KEY REFERENCES public.agent_preparation_requests(id),
    decision text NOT NULL CHECK (decision IN ('approved', 'declined')),
    note text CHECK (note IS NULL OR length(note) <= 1000),
    decided_by uuid NOT NULL REFERENCES public.console_users(id),
    proposal_id uuid REFERENCES public.console_proposals(id),
    decided_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.subscription_request_decisions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.subscription_request_decisions
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
REVOKE ALL ON public.subscription_request_decisions FROM PUBLIC, csa_app, csa_ro, csa_identity;
GRANT SELECT, INSERT ON public.subscription_request_decisions TO csa_app;
GRANT SELECT ON public.subscription_request_decisions TO csa_ro;

ALTER TABLE public.console_proposals DROP CONSTRAINT console_proposals_kind_check;
ALTER TABLE public.console_proposals ADD CONSTRAINT console_proposals_kind_check
    CHECK (kind IN ('email_export', 'training_submit', 'inbox_decision', 'subscription_action'));

CREATE INDEX subscriptions_client_status ON public.subscriptions (client_id, status);
CREATE INDEX subscriptions_client_renews
    ON public.subscriptions (client_id, renews_at) WHERE status = 'active';
CREATE INDEX subscriptions_client_pending
    ON public.subscriptions (client_id, pending_effective_at) WHERE pending_plan IS NOT NULL;
CREATE INDEX subscriptions_client_resume
    ON public.subscriptions (client_id, resume_at) WHERE status = 'paused';
CREATE INDEX agent_preparation_requests_client_agent_created
    ON public.agent_preparation_requests (client_id, agent, created_at DESC);
