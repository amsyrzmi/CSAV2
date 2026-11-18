-- Customer Inbox refinement, part 2a: a generic timer table (design
--   docs/superpowers/specs/2026-11-16-inbox-refinement-design.md, section 7; plan
--   docs/superpowers/plans/2026-11-18-inbox-refinement-2a-reminders-backend.md, task 1).
-- What it adds:
--   reminders  one row per thing to remind about, knowing nothing of what it is: kind (a handler
--              name such as inbox.overdue), subject_type and subject_id (what it is about, text so
--              any id shape fits), due_at, state, fire_count and attempts, last_fired_at and
--              last_error (what the fire job writes), a payload of ids and enums only, and
--              dedupe_key. state is one of scheduled, fired, cancelled, done (default scheduled).
--              (client_id, dedupe_key) is unique: scheduling the same key again is an upsert in
--              csa.reminders.schedule. Partial index on (due_at) WHERE state = 'scheduled' serves
--              the minute job's due scan; (client_id, kind, subject_type, subject_id) serves cancel.
-- Row security: the usual tenant_isolation policy, ENABLE and no FORCE, explicit grants (csa_ro
--   reads; csa_app SELECT, INSERT, UPDATE and DELETE).
-- Pre-check: clients exists. The table references nothing else, so it has no ordering against HR's
--   0630 to 0699 or the other 0620 to 0629 migrations.
-- Safe to run twice: CREATE TABLE and CREATE INDEX IF NOT EXISTS, the policy is dropped and made
--   again, grants are revoked and granted again.
-- Rollback: DROP TABLE public.reminders; then delete the schema_migrations row for 0623, or the
--   runner thinks it is still applied.

CREATE TABLE IF NOT EXISTS public.reminders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients (id),
    kind text NOT NULL,
    subject_type text NOT NULL,
    subject_id text NOT NULL,
    due_at timestamptz NOT NULL,
    state text NOT NULL DEFAULT 'scheduled'
        CHECK (state IN ('scheduled', 'fired', 'cancelled', 'done')),
    fire_count integer NOT NULL DEFAULT 0,
    attempts integer NOT NULL DEFAULT 0,
    last_fired_at timestamptz,
    last_error text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS reminders_due_idx
    ON public.reminders (due_at) WHERE state = 'scheduled';
CREATE INDEX IF NOT EXISTS reminders_subject_idx
    ON public.reminders (client_id, kind, subject_type, subject_id);

-- Row security and grants, as in 0621. No FORCE: the owner csa keeps bypassing the policy.
ALTER TABLE public.reminders ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.reminders;
CREATE POLICY tenant_isolation ON public.reminders
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
REVOKE ALL ON public.reminders FROM PUBLIC, csa_app, csa_ro, csa_identity;
GRANT SELECT ON public.reminders TO csa_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.reminders TO csa_app;
