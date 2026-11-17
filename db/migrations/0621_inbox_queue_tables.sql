-- Customer Inbox refinement, part 1a: the three tenant tables of the queue (design
--   docs/superpowers/specs/2026-11-16-inbox-refinement-design.md, sections 5.3 and 6.1).
-- What it adds:
--   inbox_settings      one row per company, made on its first save (no row means the defaults, which
--                       the API applies). version starts at 0 and is raised by every save.
--                       target_urgent_min 15, target_high_min 60, target_normal_min 240 and
--                       target_low_min 1440 (each 5 to 10080), repeat_minutes 60 (15 to 1440),
--                       max_repeats 3 (0 to 10), follow_up_hours 48 (0 or 1 to 720),
--                       stale_claim_hours 8 (0 or 1 to 168), handback_prompt_hours 24 (0 or 1 to 720);
--                       0 turns the last three off. The checks repeat the API's own ranges.
--   inbox_queue_events  the history of one conversation's queue: who moved it, from where to where
--                       and why. Append-only: csa_app gets SELECT and INSERT, never UPDATE or DELETE.
--                       action is one of entered, replied, claimed, unclaimed, assigned, snoozed,
--                       woke, resolved, handed_back, priority_set, topic_set, reason_changed.
--                       Index (client_id, conversation_id, id DESC) serves one conversation's
--                       history, newest first. details holds ids and enums only, never names or text.
--   inbox_saved_views   a person's saved filter set: name (1 to 60 characters), filters as JSON
--                       (validated by the API with the same model as the query parameters), private to
--                       the person (user_id). A name is unique per person and company ignoring case.
--                       The limit of 20 views per person is the API's.
-- Row security: the usual tenant_isolation policy on each table, ENABLE and no FORCE, explicit grants
--   (csa_ro reads; csa_app as listed above; saved views also UPDATE and DELETE).
-- Pre-check: 0620 (the queue columns) applied; console_users (0270) and conversations exist.
-- Safe to run twice: CREATE TABLE and CREATE INDEX IF NOT EXISTS, the policy is dropped and made again,
--   grants are revoked and granted again.
-- Rollback: DROP TABLE public.inbox_saved_views, public.inbox_queue_events, public.inbox_settings;
--   then delete the schema_migrations row for 0621, or the runner thinks it is still applied.

CREATE TABLE IF NOT EXISTS public.inbox_settings (
    client_id uuid PRIMARY KEY REFERENCES public.clients (id),
    version integer NOT NULL DEFAULT 0,
    target_urgent_min integer NOT NULL DEFAULT 15
        CHECK (target_urgent_min BETWEEN 5 AND 10080),
    target_high_min integer NOT NULL DEFAULT 60
        CHECK (target_high_min BETWEEN 5 AND 10080),
    target_normal_min integer NOT NULL DEFAULT 240
        CHECK (target_normal_min BETWEEN 5 AND 10080),
    target_low_min integer NOT NULL DEFAULT 1440
        CHECK (target_low_min BETWEEN 5 AND 10080),
    repeat_minutes integer NOT NULL DEFAULT 60 CHECK (repeat_minutes BETWEEN 15 AND 1440),
    max_repeats integer NOT NULL DEFAULT 3 CHECK (max_repeats BETWEEN 0 AND 10),
    follow_up_hours integer NOT NULL DEFAULT 48 CHECK (follow_up_hours BETWEEN 0 AND 720),
    stale_claim_hours integer NOT NULL DEFAULT 8 CHECK (stale_claim_hours BETWEEN 0 AND 168),
    handback_prompt_hours integer NOT NULL DEFAULT 24
        CHECK (handback_prompt_hours BETWEEN 0 AND 720),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.inbox_queue_events (
    id bigserial PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients (id),
    conversation_id uuid NOT NULL REFERENCES public.conversations (id),
    actor_user_id uuid REFERENCES public.console_users (id),
    action text NOT NULL CHECK (action IN (
        'entered', 'replied', 'claimed', 'unclaimed', 'assigned', 'snoozed', 'woke',
        'resolved', 'handed_back', 'priority_set', 'topic_set', 'reason_changed')),
    from_status text,
    to_status text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS inbox_queue_events_conversation_idx
    ON public.inbox_queue_events (client_id, conversation_id, id DESC);

CREATE TABLE IF NOT EXISTS public.inbox_saved_views (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients (id),
    user_id uuid NOT NULL REFERENCES public.console_users (id),
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
    filters jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS inbox_saved_views_name_key
    ON public.inbox_saved_views (client_id, user_id, lower(name));

-- Row security and grants, as in 0512. No FORCE: the owner csa keeps bypassing the policy.
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['inbox_settings', 'inbox_queue_events', 'inbox_saved_views'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON public.%I '
      'USING (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid) '
      'WITH CHECK (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid)', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, csa_app, csa_ro, csa_identity', t);
    EXECUTE format('GRANT SELECT ON public.%I TO csa_ro', t);
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON public.inbox_settings TO csa_app;
GRANT SELECT, INSERT ON public.inbox_queue_events TO csa_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.inbox_saved_views TO csa_app;
-- The event id comes from a sequence the app role has to be able to draw from.
GRANT USAGE ON SEQUENCE public.inbox_queue_events_id_seq TO csa_app;
