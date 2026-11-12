-- One customer across channels: a phone-digits normaliser, verified phones on contacts, and the
--   audit tables behind linking two contacts into one.
-- Adds:
--   public.my_phone_digits(p text): IMMUTABLE SQL. Keeps the digits of p; no digits gives ''; a
--     leading 0 becomes 60 plus the rest (Malaysian national form); a leading 60 is kept; any other
--     leading digits are kept as they are (international). NULL gives NULL. The Python mirror
--     (csa/core/phone.py) is tested equal to it.
--   contacts.phone_digits: text GENERATED ALWAYS AS (public.my_phone_digits(phone)) STORED. Adding
--     a stored generated column REWRITES the whole contacts table under an ACCESS EXCLUSIVE lock.
--   contacts.phone_verified_at (timestamptz): set only by a channel that proved the phone (a
--     WhatsApp inbound, a Telegram contact share of the sender's own number); a typed number never
--     sets it. contacts.merged_into (uuid, references contacts): the survivor, set on the losing
--     contact of a link and cleared by an unlink.
--   contacts_verified_phone_uq: unique on (client_id, phone_digits) where phone_verified_at is set,
--     the contact is not merged and the digits are not empty: one live verified holder of a number
--     per company. contacts_client_phone_digits: plain index on (client_id, phone_digits).
--   public.contact_links: one row per link (survivor, loser, reason verified_phone or staff, the ids
--     of the rows moved from the loser as jsonb, who and when, and unlinked_at/by when undone).
--   public.contact_link_reviews: one row per pair of contacts that could not be linked on their own
--     (several_matches, telegram_conflict, same_channel_identity), open or resolved (linked or
--     dismissed), with a unique partial index so a pair, in either order, has at most one open review.
--   public.contacts_belong_to_client(): BEFORE INSERT OR UPDATE trigger function on both link tables;
--     its arguments name the two contact columns of the table and it refuses a contact of another
--     company, as contact_identity_tenant_check (baseline) does for one column. Runs as the
--     invoking role (row security on contacts then also hides another company's contacts).
-- Row security on both tables: the same tenant_isolation policy as training_items (0541), ENABLE
--   only, never FORCE. SELECT, INSERT, UPDATE, DELETE to csa_app; nothing to PUBLIC, csa_ro or
--   csa_identity. No new roles. Safe to run twice: IF NOT EXISTS, CREATE OR REPLACE, policies and
--   triggers dropped and made again.
-- Pre-check: 0580 applied; contacts (baseline, 0500, 0512), clients and console_users exist. Count
--   the rows first: SELECT count(*) FROM public.contacts; the rewrite takes time in proportion to it
--   and blocks reads and writes of contacts meanwhile, so run it with the services stopped (the
--   rollout stops all four services for a migration anyway). The index creation fails, and the
--   migration with it, if one company already has two live verified contacts with the same digits;
--   none can exist before this migration because phone_verified_at is new and always NULL.
-- Rollback (as the database owner, in this order, with the services stopped): link rows moved the
--   loser's data to the survivor, so unlink every live link in the console first if any exist
--   (they cannot be undone after this).
--   DROP TABLE public.contact_link_reviews; DROP TABLE public.contact_links;
--   DROP FUNCTION public.contacts_belong_to_client();
--   DROP INDEX public.contacts_verified_phone_uq; DROP INDEX public.contacts_client_phone_digits;
--   ALTER TABLE public.contacts DROP COLUMN merged_into, DROP COLUMN phone_verified_at, DROP COLUMN
--   phone_digits (this rewrites contacts again); DROP FUNCTION public.my_phone_digits(text);
--   (the link audit and every verified stamp are lost). Finally delete the schema_migrations row for
--   0581, or the runner thinks it is still applied.

CREATE OR REPLACE FUNCTION public.my_phone_digits(p text)
RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
           WHEN d IS NULL THEN NULL
           WHEN d = '' THEN ''
           WHEN d LIKE '0%' THEN '60' || pg_catalog.substr(d, 2)
           ELSE d
         END
    FROM (SELECT pg_catalog.regexp_replace(p, '[^0-9]', '', 'g') AS d) AS digits
$$;

ALTER TABLE public.contacts
    ADD COLUMN IF NOT EXISTS phone_digits text
        GENERATED ALWAYS AS (public.my_phone_digits(phone)) STORED,
    ADD COLUMN IF NOT EXISTS phone_verified_at timestamptz,
    ADD COLUMN IF NOT EXISTS merged_into uuid REFERENCES public.contacts(id);

CREATE UNIQUE INDEX IF NOT EXISTS contacts_verified_phone_uq
    ON public.contacts (client_id, phone_digits)
    WHERE phone_verified_at IS NOT NULL AND merged_into IS NULL AND phone_digits <> '';
CREATE INDEX IF NOT EXISTS contacts_client_phone_digits
    ON public.contacts (client_id, phone_digits);

-- Both link tables name two contact columns; TG_ARGV carries their names.
CREATE OR REPLACE FUNCTION public.contacts_belong_to_client() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  col text;
  contact uuid;
BEGIN
  FOREACH col IN ARRAY TG_ARGV LOOP
    contact := (to_jsonb(NEW) ->> col)::uuid;
    IF NOT EXISTS (SELECT 1 FROM public.contacts WHERE id = contact AND client_id = NEW.client_id) THEN
      RAISE EXCEPTION 'contact % does not belong to client %', contact, NEW.client_id;
    END IF;
  END LOOP;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.contacts_belong_to_client() FROM PUBLIC, csa_ro, csa_identity;
GRANT EXECUTE ON FUNCTION public.contacts_belong_to_client() TO csa_app;

CREATE TABLE IF NOT EXISTS public.contact_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    survivor_id uuid NOT NULL REFERENCES public.contacts(id),
    loser_id uuid NOT NULL REFERENCES public.contacts(id),
    reason text NOT NULL CHECK (reason IN ('verified_phone', 'staff')),
    moved jsonb NOT NULL DEFAULT '{}',
    created_by uuid REFERENCES public.console_users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    unlinked_at timestamptz,
    unlinked_by uuid REFERENCES public.console_users(id),
    CHECK (survivor_id <> loser_id)
);

ALTER TABLE public.contact_links ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.contact_links;
CREATE POLICY tenant_isolation ON public.contact_links
    USING (client_id = nullif(current_setting('app.client_id',true),'')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id',true),'')::uuid);
REVOKE ALL ON public.contact_links FROM PUBLIC, csa_ro, csa_identity;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.contact_links TO csa_app;

DROP TRIGGER IF EXISTS trg_contact_link_tenant ON public.contact_links;
CREATE TRIGGER trg_contact_link_tenant
    BEFORE INSERT OR UPDATE OF client_id, survivor_id, loser_id ON public.contact_links
    FOR EACH ROW EXECUTE FUNCTION public.contacts_belong_to_client('survivor_id', 'loser_id');

CREATE TABLE IF NOT EXISTS public.contact_link_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    contact_a uuid NOT NULL REFERENCES public.contacts(id),
    contact_b uuid NOT NULL REFERENCES public.contacts(id),
    reason text NOT NULL
        CHECK (reason IN ('several_matches', 'telegram_conflict', 'same_channel_identity')),
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    resolution text CHECK (resolution IN ('linked', 'dismissed')),
    resolved_by uuid REFERENCES public.console_users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    CHECK (contact_a <> contact_b)
);

-- At most one open review per pair of contacts, whichever order they were written in.
CREATE UNIQUE INDEX IF NOT EXISTS contact_link_reviews_one_open
    ON public.contact_link_reviews (client_id, least(contact_a, contact_b), greatest(contact_a, contact_b))
    WHERE status = 'open';

ALTER TABLE public.contact_link_reviews ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.contact_link_reviews;
CREATE POLICY tenant_isolation ON public.contact_link_reviews
    USING (client_id = nullif(current_setting('app.client_id',true),'')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id',true),'')::uuid);
REVOKE ALL ON public.contact_link_reviews FROM PUBLIC, csa_ro, csa_identity;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.contact_link_reviews TO csa_app;

DROP TRIGGER IF EXISTS trg_contact_link_review_tenant ON public.contact_link_reviews;
CREATE TRIGGER trg_contact_link_review_tenant
    BEFORE INSERT OR UPDATE OF client_id, contact_a, contact_b ON public.contact_link_reviews
    FOR EACH ROW EXECUTE FUNCTION public.contacts_belong_to_client('contact_a', 'contact_b');
