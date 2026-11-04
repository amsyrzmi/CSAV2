-- Bookings core: people, shared things (resources), booking types, working hours, closed days,
-- blocks, attendees and the holds that make double booking impossible; plus the leave read.
-- Extends the existing bookings table (status and time checks, optimistic version, kind, source,
--   privacy, where it happens) and relaxes bookings.service so an internal meeting needs none.
-- Overlap rule: booking_holds carries one row per person or resource per booking or block, and an
--   exclusion constraint on (client_id, subject_kind, subject_id, during) refuses two overlapping
--   rows. The constraint has no status filter on purpose: cancelling a booking deletes its holds.
--   Needs the btree_gist extension (CREATE EXTENSION below; the owner role must be allowed to
--   create it). No holds are backfilled for old bookings, which name no person.
-- Leave: public.booking_leave_days(p_client, p_employees, p_from, p_to) returns only the employee
--   and the first and last day of approved leave. Never the type or reason. csa_app cannot read
--   hr_cases without an HR grant (0400), so a narrow SECURITY DEFINER function does it, in the
--   style of 0502_console_last_change.sql. It runs as the owner csa, filters by tenant itself and
--   raises when p_client is not the transaction's app.client_id.
-- Pre-check: 0100 (roles), 0270 (console_users, console_memberships) and 0380/0400 (hr_employees,
--   hr_cases) applied; nothing from 0520 onward is used. Legacy bookings.status values outside
--   (pending, confirmed, cancelled, no_show) become 'cancelled' first, with a NOTICE count. An old
--   row whose end is not after its start is left alone: the time check is added NOT VALID and is
--   validated only when no such row exists, so new rows are always checked.
-- Rollback, in this order (booking_types cannot be dropped while bookings_type_fk exists, and
--   bookings_client_id_id_key cannot be dropped while holds and attendees refer to it):
--   ALTER TABLE public.bookings DROP CONSTRAINT bookings_type_fk;
--   DROP FUNCTION public.booking_leave_days(uuid, uuid[], date, date);
--   DROP TABLE the ten booking_* tables (holds first, then attendees, blocks, closed_days, hours,
--   type_resources, type_people, types, resources, people);
--   then drop bookings_status_chk, bookings_times_chk, bookings_kind_chk, bookings_source_chk,
--   bookings_location_kind_chk, bookings_client_id_id_key, the index bookings_client_starts and
--   the columns bookings.type_id, title, kind, created_by, source, private, location_kind,
--   address, meeting_link, version, once nothing reads them. bookings.service stays nullable (set
--   it NOT NULL again only if no meeting rows remain). btree_gist stays installed. Legacy statuses
--   that became 'cancelled' above cannot be restored: the old values are not kept. Finally delete
--   the schema_migrations rows for 0510 and 0511, or the runner thinks they are still applied.
--   (0511 adds public.booking_team_members, which has its own rollback.)

DO $$
BEGIN
  IF NOT has_table_privilege('csa_app', 'public.bookings', 'UPDATE') THEN
    RAISE EXCEPTION '0510: csa_app lacks the DML grants 0100 should have given on bookings';
  END IF;
  IF to_regclass('public.hr_employees') IS NULL OR to_regclass('public.hr_cases') IS NULL
     OR to_regclass('public.console_memberships') IS NULL THEN
    RAISE EXCEPTION '0510: needs hr_employees and hr_cases (0380, 0400) and console_memberships (0270)';
  END IF;
END $$;

CREATE EXTENSION IF NOT EXISTS btree_gist;

-- bookings: new columns, normalised statuses, checks.
ALTER TABLE public.bookings ALTER COLUMN service DROP NOT NULL;

DO $$
DECLARE
  legacy_status integer;
BEGIN
  UPDATE public.bookings SET status = 'cancelled'
   WHERE status NOT IN ('pending', 'confirmed', 'cancelled', 'no_show');
  GET DIAGNOSTICS legacy_status = ROW_COUNT;
  RAISE NOTICE '0510: % booking(s) with a legacy status set to cancelled', legacy_status;
END $$;

-- (client_id, id) lets the booking tables reference a booking within one company.
ALTER TABLE public.bookings ADD CONSTRAINT bookings_client_id_id_key UNIQUE (client_id, id);

CREATE TABLE public.booking_people (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 120),
    user_id uuid,
    employee_id uuid,
    email text CHECK (email IS NULL OR length(email) BETWEEN 3 AND 254),
    is_active boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1,
    PRIMARY KEY (client_id, id),
    UNIQUE (client_id, user_id),
    FOREIGN KEY (user_id, client_id) REFERENCES public.console_memberships(user_id, client_id),
    FOREIGN KEY (client_id, employee_id) REFERENCES public.hr_employees(client_id, id)
);

CREATE TABLE public.booking_resources (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    capacity integer CHECK (capacity IS NULL OR capacity > 0),
    is_active boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1,
    PRIMARY KEY (client_id, id)
);

CREATE TABLE public.booking_types (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    duration_minutes integer NOT NULL CHECK (duration_minutes > 0),
    buffer_minutes integer NOT NULL CHECK (buffer_minutes >= 0),
    audience text NOT NULL CHECK (audience IN ('customers', 'staff', 'both')),
    delivery text NOT NULL CHECK (delivery IN ('named', 'any', 'several')),
    resource_rule text NOT NULL CHECK (resource_rule IN ('none', 'specific', 'any')),
    location_kind text NOT NULL CHECK (location_kind IN ('in_person', 'phone', 'online')),
    address text,
    meeting_link text,
    needs_confirmation boolean NOT NULL DEFAULT false,
    min_notice_minutes integer NOT NULL CHECK (min_notice_minutes >= 0),
    max_advance_days integer NOT NULL CHECK (max_advance_days >= 0),
    change_cutoff_minutes integer NOT NULL CHECK (change_cutoff_minutes >= 0),
    is_active boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1,
    PRIMARY KEY (client_id, id)
);

CREATE TABLE public.booking_type_people (
    client_id uuid NOT NULL,
    type_id uuid NOT NULL,
    person_id uuid NOT NULL,
    PRIMARY KEY (client_id, type_id, person_id),
    FOREIGN KEY (client_id, type_id) REFERENCES public.booking_types(client_id, id),
    FOREIGN KEY (client_id, person_id) REFERENCES public.booking_people(client_id, id)
);

CREATE TABLE public.booking_type_resources (
    client_id uuid NOT NULL,
    type_id uuid NOT NULL,
    resource_id uuid NOT NULL,
    PRIMARY KEY (client_id, type_id, resource_id),
    FOREIGN KEY (client_id, type_id) REFERENCES public.booking_types(client_id, id),
    FOREIGN KEY (client_id, resource_id) REFERENCES public.booking_resources(client_id, id)
);

-- weekday 0 is Monday. Times are wall-clock times in clients.timezone.
CREATE TABLE public.booking_hours (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    subject_kind text NOT NULL CHECK (subject_kind IN ('company', 'person', 'resource')),
    subject_id uuid,
    weekday smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    opens time NOT NULL,
    closes time NOT NULL,
    PRIMARY KEY (client_id, id),
    CHECK (closes > opens),
    CHECK ((subject_kind = 'company') = (subject_id IS NULL))
);
CREATE INDEX booking_hours_subject ON public.booking_hours (client_id, subject_kind, subject_id);

CREATE TABLE public.booking_closed_days (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    day date NOT NULL,
    note text CHECK (note IS NULL OR length(note) <= 200),
    PRIMARY KEY (client_id, day)
);

CREATE TABLE public.booking_blocks (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    subject_kind text NOT NULL CHECK (subject_kind IN ('person', 'resource')),
    subject_id uuid NOT NULL,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    note text CHECK (note IS NULL OR length(note) <= 200),
    created_by uuid NOT NULL REFERENCES public.console_users(id),
    PRIMARY KEY (client_id, id),
    CHECK (ends_at > starts_at)
);
CREATE INDEX booking_blocks_subject
    ON public.booking_blocks (client_id, subject_kind, subject_id, starts_at);

-- bookings: columns after the tables they point at.
ALTER TABLE public.bookings
    ADD COLUMN type_id uuid,
    ADD COLUMN title text,
    ADD COLUMN kind text NOT NULL DEFAULT 'appointment',
    ADD COLUMN created_by uuid REFERENCES public.console_users(id),
    ADD COLUMN source text NOT NULL DEFAULT 'staff',
    ADD COLUMN private boolean NOT NULL DEFAULT false,
    ADD COLUMN location_kind text,
    ADD COLUMN address text,
    ADD COLUMN meeting_link text,
    ADD COLUMN version integer NOT NULL DEFAULT 1;
ALTER TABLE public.bookings ADD CONSTRAINT bookings_type_fk
    FOREIGN KEY (client_id, type_id) REFERENCES public.booking_types(client_id, id);
ALTER TABLE public.bookings
    ADD CONSTRAINT bookings_status_chk
        CHECK (status IN ('pending', 'confirmed', 'cancelled', 'no_show')),
    ADD CONSTRAINT bookings_kind_chk CHECK (kind IN ('appointment', 'meeting')),
    ADD CONSTRAINT bookings_source_chk CHECK (source IN ('staff', 'assistant', 'bot')),
    ADD CONSTRAINT bookings_location_kind_chk
        CHECK (location_kind IS NULL OR location_kind IN ('in_person', 'phone', 'online')),
    ADD CONSTRAINT bookings_times_chk CHECK (ends_at > starts_at) NOT VALID;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM public.bookings WHERE ends_at <= starts_at) THEN
    RAISE NOTICE '0510: old booking(s) end at or before they start; bookings_times_chk left NOT VALID';
  ELSE
    ALTER TABLE public.bookings VALIDATE CONSTRAINT bookings_times_chk;
  END IF;
END $$;
ALTER TABLE public.bookings VALIDATE CONSTRAINT bookings_status_chk;
CREATE INDEX bookings_client_starts ON public.bookings (client_id, starts_at);

CREATE TABLE public.booking_attendees (
    client_id uuid NOT NULL,
    booking_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    person_id uuid,
    guest_name text CHECK (guest_name IS NULL OR length(btrim(guest_name)) BETWEEN 1 AND 120),
    guest_email text CHECK (guest_email IS NULL OR length(guest_email) BETWEEN 3 AND 254),
    PRIMARY KEY (client_id, id),
    CHECK (num_nonnulls(person_id, guest_email) = 1),
    FOREIGN KEY (client_id, booking_id) REFERENCES public.bookings(client_id, id),
    FOREIGN KEY (client_id, person_id) REFERENCES public.booking_people(client_id, id)
);
CREATE INDEX booking_attendees_booking ON public.booking_attendees (client_id, booking_id);

-- One row per person or resource per booking or block. The exclusion constraint is the only
-- overlap rule: two overlapping rows for one subject cannot both commit.
CREATE TABLE public.booking_holds (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    booking_id uuid,
    block_id uuid,
    subject_kind text NOT NULL CHECK (subject_kind IN ('person', 'resource')),
    subject_id uuid NOT NULL,
    during tstzrange NOT NULL CHECK (NOT isempty(during)),
    PRIMARY KEY (client_id, id),
    CHECK (num_nonnulls(booking_id, block_id) = 1),
    FOREIGN KEY (client_id, booking_id) REFERENCES public.bookings(client_id, id) ON DELETE CASCADE,
    FOREIGN KEY (client_id, block_id) REFERENCES public.booking_blocks(client_id, id) ON DELETE CASCADE,
    CONSTRAINT booking_holds_no_overlap
        EXCLUDE USING gist (client_id WITH =, subject_kind WITH =, subject_id WITH =, during WITH &&)
);
CREATE INDEX booking_holds_booking ON public.booking_holds (client_id, booking_id)
    WHERE booking_id IS NOT NULL;
CREATE INDEX booking_holds_block ON public.booking_holds (client_id, block_id)
    WHERE block_id IS NOT NULL;

-- Row security and grants. The 0110 loop ran before these tables existed, so do it here.
-- No FORCE: the owner csa keeps bypassing the policy.
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['booking_people', 'booking_resources', 'booking_types',
      'booking_type_people', 'booking_type_resources', 'booking_hours', 'booking_closed_days',
      'booking_blocks', 'booking_attendees', 'booking_holds'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON public.%I '
      'USING (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid) '
      'WITH CHECK (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid)', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, csa_app, csa_ro, csa_identity', t);
    EXECUTE format('GRANT SELECT ON public.%I TO csa_ro', t);
  END LOOP;
END $$;
-- People, resources and types are deactivated, never deleted. Every other table is rewritten
-- or emptied by the app (cancelling a booking deletes its holds).
GRANT SELECT, INSERT, UPDATE ON public.booking_people, public.booking_resources,
    public.booking_types TO csa_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.booking_type_people,
    public.booking_type_resources, public.booking_hours, public.booking_closed_days,
    public.booking_blocks, public.booking_attendees, public.booking_holds TO csa_app;

-- Approved leave, dates only.
CREATE OR REPLACE FUNCTION public.booking_leave_days(
    p_client uuid, p_employees uuid[], p_from date, p_to date)
RETURNS TABLE(employee_id uuid, starts_on date, ends_on date)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF p_client IS DISTINCT FROM nullif(pg_catalog.current_setting('app.client_id', true), '')::uuid
    THEN
        RAISE EXCEPTION 'booking_leave_days: not this company' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
    SELECT c.employee_id, (c.payload ->> 'starts_on')::date, (c.payload ->> 'ends_on')::date
      FROM public.hr_cases c
     WHERE c.client_id = p_client
       AND c.domain = 'leave'
       AND c.status = 'approved'
       AND c.employee_id = ANY (p_employees)
       AND (c.payload ->> 'starts_on')::date <= p_to
       AND (c.payload ->> 'ends_on')::date >= p_from
     ORDER BY 2, 1;
END $$;

ALTER FUNCTION public.booking_leave_days(uuid, uuid[], date, date) OWNER TO csa;
REVOKE ALL ON FUNCTION public.booking_leave_days(uuid, uuid[], date, date)
    FROM PUBLIC, csa_ro, csa_identity;
GRANT EXECUTE ON FUNCTION public.booking_leave_days(uuid, uuid[], date, date) TO csa_app;
