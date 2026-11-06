-- Bookings stage 2: what Google Calendar needs.
-- booking_calendar_connections: one row per person who connects their own Google account, and
--   per member (both unique). The grant is the one scope calendar.app.created, kept encrypted
--   (access_token and refresh_token hold csa.core.crypto output; nothing here is plaintext).
--   calendar_id is the secondary "Bookings" calendar the app created in that person's account,
--   the only calendar the grant can touch. status is pending (connecting), connected, or
--   needs_reconnect (Google refused the refresh token). Unlike email_mailboxes, which only its own
--   user may read, the policy is plain tenant isolation: the push runs as the system for other
--   people's bookings. Tokens are never returned by any API, and only the sync code decrypts them.
-- booking_calendar_oauth_states: the PKCE state of a connection in progress, written when the
--   person starts connecting and consumed by the callback, as email_oauth_states does. It goes
--   with its connection.
-- booking_calendar_events: a ledger of what Google holds, one row per booking and person. The
--   booking is the only source of what it should hold: a row is out of date when its
--   synced_version is behind bookings.version (or it has no event yet), and the reconcile job
--   makes it match. status is pending, synced, failed, deleted (the event was removed for a
--   cancelled booking) or dropped (the person disconnected). last_error is a category, never text
--   from Google.
-- Row security on all three tables, never FORCE: the owner csa keeps bypassing the policy. csa_ro
--   and csa_identity get nothing, so a read-only role never sees a token.

CREATE TABLE public.booking_calendar_connections (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    person_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'connected', 'needs_reconnect')),
    calendar_id text CHECK (calendar_id IS NULL OR length(calendar_id) BETWEEN 1 AND 256),
    access_token text,
    refresh_token text,
    token_expires_at timestamptz,
    scopes text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, id),
    UNIQUE (client_id, user_id),
    UNIQUE (client_id, person_id),
    FOREIGN KEY (user_id, client_id) REFERENCES public.console_memberships(user_id, client_id),
    FOREIGN KEY (client_id, person_id) REFERENCES public.booking_people(client_id, id)
);

CREATE TABLE public.booking_calendar_oauth_states (
    state_hash text PRIMARY KEY CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    client_id uuid NOT NULL,
    user_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    code_verifier text NOT NULL CHECK (length(code_verifier) BETWEEN 43 AND 128),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    FOREIGN KEY (user_id, client_id) REFERENCES public.console_memberships(user_id, client_id),
    FOREIGN KEY (client_id, connection_id)
        REFERENCES public.booking_calendar_connections(client_id, id) ON DELETE CASCADE
);

CREATE TABLE public.booking_calendar_events (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    booking_id uuid NOT NULL,
    person_id uuid NOT NULL,
    google_event_id text CHECK (google_event_id IS NULL OR length(google_event_id) BETWEEN 1 AND 256),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'synced', 'failed', 'deleted', 'dropped')),
    synced_version integer,
    -- A failure is counted against the booking version it was tried at: attempts is how many times
    -- failed_version has failed, and a booking that changes again gets a fresh set of attempts.
    failed_version integer,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error text CHECK (last_error IS NULL OR length(last_error) <= 64),
    -- The mail about this booking to this person left out the calendar file, because their Google
    -- event holds it. A cancellation sends the cancel file to everyone else who has a row.
    file_free boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, booking_id, person_id),
    FOREIGN KEY (client_id, booking_id) REFERENCES public.bookings(client_id, id),
    FOREIGN KEY (client_id, person_id) REFERENCES public.booking_people(client_id, id)
);
-- The reconcile sweep reads the rows that still need work.
CREATE INDEX booking_calendar_events_open ON public.booking_calendar_events (client_id, status)
    WHERE status IN ('pending', 'failed');

DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['booking_calendar_connections', 'booking_calendar_oauth_states',
      'booking_calendar_events'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON public.%I '
      'USING (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid) '
      'WITH CHECK (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid)', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, csa_app, csa_ro, csa_identity', t);
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.booking_calendar_connections,
    public.booking_calendar_events TO csa_app;
GRANT SELECT, INSERT, DELETE ON public.booking_calendar_oauth_states TO csa_app;
