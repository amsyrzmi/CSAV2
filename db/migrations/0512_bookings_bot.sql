-- Bookings, PR 2: what the customer bot, the reminders and the staff alert need.
-- bookings gets three nullable columns: replaces_booking_id (the booking this one moves, in the
--   same company), conversation_id (the chat the booking came from) and expires_at (when a pending
--   booking lapses). Old rows and staff bookings leave all three empty. It also gets
--   late_for_reminder: true when the booking's current start was set (made, moved, or moved by a
--   confirmed request) after its reminder was due, judged with the company's reminder time at that
--   moment. Such a booking gets no reminder: its confirmation is enough. Every write of a start
--   sets it again; old rows are false, so they are reminded as before. Two partial indexes:
--   bookings_pending_changes (the live requests to move a booking, read for every calendar row
--   and locked by every cancel, decline and move) and bookings_contact (a customer's own bookings,
--   read on every customer turn at a company whose customers can book).
-- contacts gets customer_given_email, a nullable address a customer typed into a chat. It is a
--   claim nobody has checked, so it is kept apart from contacts.email: the walk-in match, the
--   Inbox's "known customer" and every uniqueness rule use email only. Booking mail and
--   reminders use coalesce(email, customer_given_email). It has a length check and no
--   uniqueness, and the bot fills it only while it is empty.
-- booking_turn_actions: one row per customer message key and company, written by every bot write
--   (book, change, cancel) in the write's own savepoint. It says which booking the key made, moved
--   or cancelled, so a retried message answers with that instead of acting again, and a late retry
--   of an old message never undoes a later one. The primary key (client_id, turn_key) is the
--   backstop: one message does one thing. action is book, change or cancel. Rows are written once
--   and never changed or removed.
-- booking_settings: one row per company, made on its first save. reminder_minutes (15 to 10080),
--   pending_hours (1 to 168) and customer_limit (1 to 50) have checks as well as the API's own
--   range. No row means the defaults, 1440, 24 and 5.
-- booking_reminders: one row per booking, start time, audience and address, so a reminder is made
--   once and a moved booking gets a fresh one. address is NULL for a Telegram row and set for an
--   email row (a check), and always lower case (a check), so two spellings of one address are one
--   reminder. The unique constraint is NULLS NOT DISTINCT (Postgres 15 and later; the image is 16)
--   so two Telegram rows for one booking still collide. audience is customer, staff (people and
--   guests on the booking) or lapse (the email that tells a customer without Telegram that the
--   bot's pending booking ran out of time); the worker's mail job claims queued email rows through
--   the partial index booking_reminders_queued, so the sweep never reads the rows already settled.
-- staff_notification_targets.events gains 'booking_pending': the column default (set in 0160) has it
--   and so does every existing target that already lists 'approval_needed'. A target that chose its
--   own events without approval_needed is left alone.
-- Not here: the booking_action kind and action on console_proposals and console_workspace_turns.
--   0521 rebuilds those constraints with fixed lists after this file on a fresh database, so they
--   are added by 0531 (db/migrations/0531_booking_assistant_action.sql).
-- Row security: the usual tenant_isolation policy on the three new tables, no FORCE, grants to
--   csa_app only (csa_ro reads). Reminders are never deleted, only marked; turn actions are only
--   inserted.
-- Pre-check: 0510 (bookings, its (client_id, id) key) and 0160 (staff_notification_targets) applied.
--   0520 and later are not used. Not safe to run twice: the columns and tables are created plain.
-- Rollback, in this order:
--   DROP TABLE public.booking_turn_actions, public.booking_reminders, public.booking_settings;
--   DROP INDEX public.bookings_pending_changes, public.bookings_contact;
--   ALTER TABLE public.bookings DROP CONSTRAINT bookings_replaces_fk,
--     DROP COLUMN replaces_booking_id, DROP COLUMN conversation_id, DROP COLUMN expires_at,
--     DROP COLUMN late_for_reminder;
--     (bookings that were made by the bot stay)
--   ALTER TABLE public.contacts DROP COLUMN customer_given_email;
--   ALTER TABLE public.staff_notification_targets ALTER COLUMN events SET DEFAULT
--     '{escalation,approval_needed,order_submitted,dlq,circuit_opened}';
--   UPDATE public.staff_notification_targets SET events = array_remove(events, 'booking_pending');
--   Finally delete the schema_migrations row for 0512, or the runner thinks it is still applied.

DO $$
BEGIN
  IF to_regclass('public.booking_types') IS NULL
     OR to_regclass('public.staff_notification_targets') IS NULL THEN
    RAISE EXCEPTION '0512: needs booking_types (0510) and staff_notification_targets (0160)';
  END IF;
  IF NOT has_table_privilege('csa_app', 'public.bookings', 'UPDATE') THEN
    RAISE EXCEPTION '0512: csa_app lacks the DML grants 0100 should have given on bookings';
  END IF;
END $$;

ALTER TABLE public.bookings
    ADD COLUMN replaces_booking_id uuid,
    ADD COLUMN conversation_id uuid,
    ADD COLUMN expires_at timestamptz,
    ADD COLUMN late_for_reminder boolean NOT NULL DEFAULT false;
ALTER TABLE public.bookings ADD CONSTRAINT bookings_replaces_fk
    FOREIGN KEY (client_id, replaces_booking_id) REFERENCES public.bookings(client_id, id);
-- The live request to move a booking: the calendar looks for one on every row it shows, and every
-- cancel, decline and move locks them. Only pending rows are ever asked for.
CREATE INDEX bookings_pending_changes ON public.bookings (client_id, replaces_booking_id)
    WHERE status = 'pending';
-- A customer's own bookings: the bot's context on every customer turn, their list, the limit and
-- the "already holds this time" check. Staff meetings with no customer are left out.
CREATE INDEX bookings_contact ON public.bookings (client_id, contact_id, starts_at)
    WHERE contact_id IS NOT NULL;

ALTER TABLE public.contacts
    ADD COLUMN customer_given_email text,
    ADD CONSTRAINT contacts_customer_given_email_length
        CHECK (customer_given_email IS NULL OR length(customer_given_email) BETWEEN 3 AND 254);

CREATE TABLE public.booking_turn_actions (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    turn_key text NOT NULL,
    booking_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('book', 'change', 'cancel')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, turn_key),
    FOREIGN KEY (client_id, booking_id) REFERENCES public.bookings(client_id, id)
);

CREATE TABLE public.booking_settings (
    client_id uuid PRIMARY KEY REFERENCES public.clients(id),
    reminder_minutes integer NOT NULL DEFAULT 1440
        CHECK (reminder_minutes BETWEEN 15 AND 10080),
    pending_hours integer NOT NULL DEFAULT 24 CHECK (pending_hours BETWEEN 1 AND 168),
    customer_limit integer NOT NULL DEFAULT 5 CHECK (customer_limit BETWEEN 1 AND 50),
    version integer NOT NULL DEFAULT 1
);

CREATE TABLE public.booking_reminders (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    booking_id uuid NOT NULL,
    starts_at timestamptz NOT NULL,
    audience text NOT NULL CHECK (audience IN ('customer', 'staff', 'lapse')),
    channel text NOT NULL CHECK (channel IN ('telegram', 'email')),
    address text CHECK (address IS NULL OR length(address) BETWEEN 3 AND 254),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'sending', 'sent', 'failed', 'dropped')),
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    PRIMARY KEY (client_id, id),
    FOREIGN KEY (client_id, booking_id) REFERENCES public.bookings(client_id, id),
    CONSTRAINT booking_reminders_address_by_channel
        CHECK ((channel = 'email') = (address IS NOT NULL)),
    CONSTRAINT booking_reminders_address_lower CHECK (address IS NULL OR address = lower(address)),
    CONSTRAINT booking_reminders_once
        UNIQUE NULLS NOT DISTINCT (client_id, booking_id, starts_at, audience, address)
);
CREATE INDEX booking_reminders_queued ON public.booking_reminders (created_at, id)
    WHERE channel = 'email' AND status = 'queued';

-- Row security and grants, as in 0510. No FORCE: the owner csa keeps bypassing the policy.
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['booking_settings', 'booking_reminders', 'booking_turn_actions'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON public.%I '
      'USING (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid) '
      'WITH CHECK (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid)', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, csa_app, csa_ro, csa_identity', t);
    EXECUTE format('GRANT SELECT ON public.%I TO csa_ro', t);
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON public.booking_settings, public.booking_reminders TO csa_app;
GRANT SELECT, INSERT ON public.booking_turn_actions TO csa_app;

-- A new booking waiting for staff is an event a target can ask for.
ALTER TABLE public.staff_notification_targets ALTER COLUMN events SET DEFAULT
    '{escalation,approval_needed,order_submitted,dlq,circuit_opened,booking_pending}';
UPDATE public.staff_notification_targets
   SET events = array_append(events, 'booking_pending')
 WHERE 'approval_needed' = ANY (events) AND NOT 'booking_pending' = ANY (events);
