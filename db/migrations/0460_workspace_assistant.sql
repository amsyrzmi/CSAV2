-- Cross-tab workspace assistant: proposal registry, company directory view, relaxed turns.
-- Preconditions: 0450 applied (0370 workspace tables, 0380 HR tables, 0440 console sessions).
-- Rollback: DROP VIEW public.company_directory; DROP TABLE public.console_proposals (with its
--   stored card and claim lease columns);
--   then restore on public.console_workspace_turns the CHECKs
--   console_workspace_turns_action_check (action IN ('page_guidance','local_order_read')),
--   console_workspace_turns_page_key_check (page_key IN ('workspace_home','business_orders',
--   'business_order')) and the combined action/resource_id CHECK from 0370.
CREATE TABLE public.console_proposals (
    id uuid PRIMARY KEY DEFAULT public.uuid_generate_v4(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    user_id uuid NOT NULL REFERENCES public.console_users(id),
    login_session_id uuid NOT NULL,
    thread_id uuid,
    kind text NOT NULL CHECK (kind IN ('email_export','training_submit','inbox_decision')),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    expected_version text,
    card jsonb NOT NULL CHECK (jsonb_typeof(card) = 'object'),
    status text NOT NULL CHECK (status IN ('requested','confirmed','executed','expired','cancelled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    executed_at timestamptz,
    result jsonb,
    -- Exclusive execution lease: set when a confirm claims the row, checked on revert/record.
    claim_token uuid,
    claimed_at timestamptz,
    CHECK (expires_at > created_at AND expires_at <= created_at + interval '15 minutes'),
    CHECK (status <> 'confirmed' OR (claim_token IS NOT NULL AND claimed_at IS NOT NULL)),
    FOREIGN KEY (login_session_id, user_id) REFERENCES public.console_sessions(id, user_id)
);
CREATE INDEX console_proposals_owner_status
    ON public.console_proposals (client_id, user_id, status);

ALTER TABLE public.console_proposals ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.console_proposals
    USING (client_id = nullif(current_setting('app.client_id',true),'')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id',true),'')::uuid);
REVOKE ALL ON public.console_proposals FROM PUBLIC, csa_app, csa_ro, csa_identity;
GRANT SELECT, INSERT, UPDATE ON public.console_proposals TO csa_app;

-- Relax the workspace turn CHECKs; the combined action/resource CHECK goes because the
-- new actions carry no resource_id. Unknown actions and page keys are still rejected.
ALTER TABLE public.console_workspace_turns
    DROP CONSTRAINT console_workspace_turns_action_check,
    DROP CONSTRAINT console_workspace_turns_page_key_check,
    DROP CONSTRAINT console_workspace_turns_check;
ALTER TABLE public.console_workspace_turns
    ADD CONSTRAINT console_workspace_turns_action_check CHECK (action IN (
        'answer','navigate','directory_lookup','email_compose','training_submit',
        'inbox_decision','clarification','page_guidance','local_order_read')),
    ADD CONSTRAINT console_workspace_turns_page_key_check CHECK (page_key IN (
        'workspace_home','business_orders','business_order','overview','services','inbox',
        'knowledge_base','business','hr','email','activity','chat'));

-- Owner-run security_barrier view: reads hr_employees through the owner (RLS is not FORCEd),
-- so csa_app needs no app.hr_user_id; tenant scoping is the explicit client_id filter.
CREATE VIEW public.company_directory WITH (security_barrier = true) AS
SELECT e.client_id, e.id AS employee_id, a.user_id, e.display_name, e.job_title, e.work_email
FROM public.hr_employees e
LEFT JOIN public.hr_employee_accounts a
    ON a.client_id = e.client_id AND a.employee_id = e.id
WHERE e.is_active
  AND e.client_id = nullif(current_setting('app.client_id',true),'')::uuid;
REVOKE ALL ON public.company_directory FROM PUBLIC, csa_app, csa_ro, csa_identity;
GRANT SELECT ON public.company_directory TO csa_app;
