-- Payments 1, core: what an invoice needs to be issued with a reference, paid on a ledger, reminded
--   and answered about. The platform never holds money: nothing here is a gateway, card or provider.
-- invoices (baseline, no migration touched it): the status list gets a check (draft, issued,
--   proof_submitted, paid, overdue, void, cash_pending) and amount must be 0 or more. New nullable
--   columns: reference (the customer-visible code, XXXX-XXC: six characters from 2-9 and A-Z without
--   I, L, O, a dash after the fourth, one check character; unique per company), method_kind (qr,
--   transfer or cash), subscription_id with period_start and period_end (the billing period the
--   invoice is for; the opening invoice of a pending subscription has no period_start until it is
--   paid), reminded_at, claim_note and claimed_at (a customer said they paid), void_reason, voided_at
--   and voided_by (console_users). invoices_client_id_key and subscriptions_client_id_key are
--   UNIQUE (client_id, id) so child rows can reference an invoice, and an invoice a subscription, by
--   company and id: another company's row cannot be named. invoices_subscription_period_uq allows
--   one invoice per (company, subscription, period_start), void ones included, with NULLS NOT
--   DISTINCT (Postgres 15 and later; the image is 16) so two opening invoices of one subscription
--   collide; invoices without a subscription never do. Plus invoices_reference_uq, invoices_open
--   (the live invoices a company still waits for) and invoices_contact (a customer's own invoices).
-- subscriptions.pause_reason: NULL, or 'unpaid' when the company's unpaid rule paused the subscription.
-- payment_settings: one row per company at most (no row means switched off): enabled, lead_days (1 to
--   30), reminder_days (days relative to the due date; at most 6, each -30 to 60), suspend_after_days
--   (NULL or 1 to 90) and a version for optimistic updates.
-- payment_methods: how a company takes payment (qr, transfer or cash): label, details (a JSON object,
--   for example the account number), instructions, is_active, sort_order and a version.
-- payments: the ledger, append-only for csa_app (SELECT and INSERT; no UPDATE, DELETE or TRUNCATE).
--   An invoice is paid in the transaction that inserts its row. UNIQUE (client_id, idempotency_key)
--   makes a retry the same payment, and payments_one_per_invoice (client_id, invoice_id) where the
--   invoice is set lets an invoice be paid only once. invoice_id is NULL for a statement line that
--   matched nothing. statement_line_id has no foreign key yet (Payments 2 adds its table). Amounts are
--   numeric(12,2) with a three-letter upper-case currency, never floating point.
-- invoice_reminders: one row per (invoice, step), so a reminder is made once and an older owed step
--   is recorded as skipped. channel is telegram, whatsapp, email or none; address is set for an email
--   row and always lower case; status is queued, sending, sent, failed, dropped, skipped or
--   no_channel. Partial index invoice_reminders_queued serves the mail sweep. Rows are never deleted.
-- Row security on the four new tables: the same tenant_isolation policy as training_items (0541),
--   ENABLE only, never FORCE. REVOKE ALL from PUBLIC, csa_app, csa_ro and csa_identity, then grants to
--   csa_app only: payments SELECT, INSERT; invoice_reminders and payment_settings SELECT, INSERT,
--   UPDATE; payment_methods SELECT, INSERT, UPDATE, DELETE. Child rows reference invoices by
--   (client_id, invoice_id). None of the four has a contact_id: invoices already move with a contact
--   link (csa/contacts/linking.py). Constraints on invoices and subscriptions are added NOT VALID,
--   then validated (both tables are small). Safe to run twice: IF NOT EXISTS, constraints checked
--   for by name, policies dropped and made again.
-- Pre-check: 0581 applied; invoices, subscriptions, console_users and clients exist; csa_app can
--   UPDATE invoices (0100). Count the rows first:
--   SELECT status, count(*) FROM public.invoices GROUP BY 1; the migration stops with
--   "0590: invoices hold statuses outside the new list: ..." if any row has a status outside the
--   seven, and with a similar message for a negative amount. Fix those rows (an invoice of an unknown
--   status becomes 'void' or one of the seven) and run it again. Adding the two unique constraints
--   builds an index over invoices and over subscriptions under a short lock.
-- Rollback (as the database owner, with the services stopped; the older image's readiness check wants
--   the highest applied version to equal its own), in this order, after 0591 is rolled back:
--   DROP TABLE public.invoice_reminders, public.payments, public.payment_methods,
--   public.payment_settings;  (every payment, reminder, method and setting is lost);
--   ALTER TABLE public.subscriptions DROP CONSTRAINT subscriptions_pause_reason_chk,
--     DROP COLUMN pause_reason;
--   DROP INDEX public.invoices_reference_uq, public.invoices_subscription_period_uq,
--     public.invoices_open, public.invoices_contact;
--   ALTER TABLE public.invoices DROP CONSTRAINT invoices_subscription_fk, DROP CONSTRAINT
--     invoices_status_chk, DROP CONSTRAINT invoices_amount_chk, DROP CONSTRAINT
--     invoices_client_id_key, DROP COLUMN reference, DROP COLUMN method_kind, DROP COLUMN
--     subscription_id, DROP COLUMN period_start, DROP COLUMN period_end, DROP COLUMN reminded_at,
--     DROP COLUMN claim_note, DROP COLUMN claimed_at, DROP COLUMN void_reason, DROP COLUMN
--     voided_at, DROP COLUMN voided_by;  (the check constraints on the dropped columns go with them)
--   ALTER TABLE public.subscriptions DROP CONSTRAINT subscriptions_client_id_key;
--   Finally delete the schema_migrations row for 0590, or the runner thinks it is still applied.

DO $$
DECLARE
  unknown text;
BEGIN
  IF to_regclass('public.invoices') IS NULL
     OR to_regclass('public.subscriptions') IS NULL
     OR to_regclass('public.console_users') IS NULL THEN
    RAISE EXCEPTION '0590: needs invoices, subscriptions and console_users';
  END IF;
  IF NOT has_table_privilege('csa_app', 'public.invoices', 'UPDATE') THEN
    RAISE EXCEPTION '0590: csa_app lacks the DML grants 0100 should have given on invoices';
  END IF;
  SELECT string_agg(DISTINCT status, ', ' ORDER BY status) INTO unknown
    FROM public.invoices
   WHERE status NOT IN ('draft', 'issued', 'proof_submitted', 'paid', 'overdue', 'void',
                        'cash_pending');
  IF unknown IS NOT NULL THEN
    RAISE EXCEPTION '0590: invoices hold statuses outside the new list: %', unknown;
  END IF;
  IF EXISTS (SELECT 1 FROM public.invoices WHERE amount < 0) THEN
    RAISE EXCEPTION '0590: invoices hold a negative amount';
  END IF;
END $$;

ALTER TABLE public.invoices
    ADD COLUMN IF NOT EXISTS reference text,
    ADD COLUMN IF NOT EXISTS method_kind text,
    ADD COLUMN IF NOT EXISTS subscription_id uuid,
    ADD COLUMN IF NOT EXISTS period_start timestamptz,
    ADD COLUMN IF NOT EXISTS period_end timestamptz,
    ADD COLUMN IF NOT EXISTS reminded_at timestamptz,
    ADD COLUMN IF NOT EXISTS claim_note text,
    ADD COLUMN IF NOT EXISTS claimed_at timestamptz,
    ADD COLUMN IF NOT EXISTS void_reason text,
    ADD COLUMN IF NOT EXISTS voided_at timestamptz,
    ADD COLUMN IF NOT EXISTS voided_by uuid REFERENCES public.console_users(id);

ALTER TABLE public.subscriptions ADD COLUMN IF NOT EXISTS pause_reason text;

-- Each spec: table, constraint, definition. Checks and foreign keys go in NOT VALID, then validate.
DO $$
DECLARE
  spec text[];
BEGIN
  FOREACH spec SLICE 1 IN ARRAY ARRAY[
      ARRAY['invoices', 'invoices_status_chk',
            'CHECK (status IN (''draft'', ''issued'', ''proof_submitted'', ''paid'', ''overdue'','
            || ' ''void'', ''cash_pending''))'],
      ARRAY['invoices', 'invoices_amount_chk', 'CHECK (amount >= 0)'],
      ARRAY['invoices', 'invoices_reference_chk',
            'CHECK (reference IS NULL OR reference ~ ''^[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{3}$'')'],
      ARRAY['invoices', 'invoices_method_kind_chk',
            'CHECK (method_kind IN (''qr'', ''transfer'', ''cash''))'],
      ARRAY['invoices', 'invoices_period_chk',
            'CHECK (period_start IS NULL OR period_end IS NULL OR period_end > period_start)'],
      ARRAY['invoices', 'invoices_claim_note_chk', 'CHECK (char_length(claim_note) <= 300)'],
      ARRAY['invoices', 'invoices_void_reason_chk', 'CHECK (char_length(void_reason) <= 300)'],
      ARRAY['subscriptions', 'subscriptions_pause_reason_chk',
            'CHECK (pause_reason IN (''unpaid''))']] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                    WHERE conrelid = ('public.' || spec[1])::regclass AND conname = spec[2]) THEN
      EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I %s NOT VALID',
                     spec[1], spec[2], spec[3]);
    END IF;
    EXECUTE format('ALTER TABLE public.%I VALIDATE CONSTRAINT %I', spec[1], spec[2]);
  END LOOP;

  -- The keys that let child rows name a row by company and id.
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                  WHERE conrelid = 'public.invoices'::regclass
                    AND conname = 'invoices_client_id_key') THEN
    ALTER TABLE public.invoices ADD CONSTRAINT invoices_client_id_key UNIQUE (client_id, id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                  WHERE conrelid = 'public.subscriptions'::regclass
                    AND conname = 'subscriptions_client_id_key') THEN
    ALTER TABLE public.subscriptions
        ADD CONSTRAINT subscriptions_client_id_key UNIQUE (client_id, id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                  WHERE conrelid = 'public.invoices'::regclass
                    AND conname = 'invoices_subscription_fk') THEN
    ALTER TABLE public.invoices
        ADD CONSTRAINT invoices_subscription_fk FOREIGN KEY (client_id, subscription_id)
        REFERENCES public.subscriptions (client_id, id) NOT VALID;
  END IF;
  ALTER TABLE public.invoices VALIDATE CONSTRAINT invoices_subscription_fk;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS invoices_reference_uq
    ON public.invoices (client_id, reference) WHERE reference IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS invoices_subscription_period_uq
    ON public.invoices (client_id, subscription_id, period_start) NULLS NOT DISTINCT
    WHERE subscription_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS invoices_open
    ON public.invoices (client_id, due_at)
    WHERE status IN ('issued', 'overdue', 'proof_submitted', 'cash_pending');
CREATE INDEX IF NOT EXISTS invoices_contact
    ON public.invoices (client_id, contact_id, issued_at DESC) WHERE contact_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.payment_settings (
    client_id uuid PRIMARY KEY REFERENCES public.clients(id),
    enabled boolean NOT NULL DEFAULT false,
    lead_days integer NOT NULL DEFAULT 7 CHECK (lead_days BETWEEN 1 AND 30),
    reminder_days integer[] NOT NULL DEFAULT '{-7,0,3,7}'
        CHECK (cardinality(reminder_days) <= 6
               AND array_position(reminder_days, NULL) IS NULL
               AND -30 <= ALL (reminder_days) AND 60 >= ALL (reminder_days)),
    suspend_after_days integer CHECK (suspend_after_days BETWEEN 1 AND 90),
    version integer NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.payment_methods (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    kind text NOT NULL CHECK (kind IN ('qr', 'transfer', 'cash')),
    label text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 60),
    details jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details) = 'object'),
    instructions text NOT NULL DEFAULT '' CHECK (char_length(instructions) <= 500),
    is_active boolean NOT NULL DEFAULT true,
    sort_order integer NOT NULL DEFAULT 0 CHECK (sort_order BETWEEN 0 AND 100),
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS payment_methods_client_active
    ON public.payment_methods (client_id, is_active, sort_order);

CREATE TABLE IF NOT EXISTS public.payments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    invoice_id text,
    amount numeric(12,2) NOT NULL CHECK (amount > 0),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    received_at timestamptz NOT NULL,
    method_kind text CHECK (method_kind IN ('qr', 'transfer', 'cash')),
    source text NOT NULL CHECK (source IN ('statement', 'staff', 'cash')),
    statement_line_id uuid,
    reference_seen text CHECK (char_length(reference_seen) <= 64),
    matched_by text NOT NULL CHECK (matched_by IN ('auto', 'staff')),
    created_by uuid REFERENCES public.console_users(id),
    note text CHECK (char_length(note) <= 300),
    idempotency_key text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT payments_invoice_fk FOREIGN KEY (client_id, invoice_id)
        REFERENCES public.invoices (client_id, id),
    CONSTRAINT payments_idempotency_uq UNIQUE (client_id, idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS payments_one_per_invoice
    ON public.payments (client_id, invoice_id) WHERE invoice_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.invoice_reminders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    invoice_id text NOT NULL,
    step integer NOT NULL CHECK (step BETWEEN -30 AND 60),
    channel text NOT NULL CHECK (channel IN ('telegram', 'whatsapp', 'email', 'none')),
    address text CHECK (char_length(address) BETWEEN 3 AND 254 AND address = lower(address)),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'sending', 'sent', 'failed', 'dropped', 'skipped',
                          'no_channel')),
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    CONSTRAINT invoice_reminders_invoice_fk FOREIGN KEY (client_id, invoice_id)
        REFERENCES public.invoices (client_id, id),
    CONSTRAINT invoice_reminders_step_uq UNIQUE (client_id, invoice_id, step)
);
CREATE INDEX IF NOT EXISTS invoice_reminders_queued
    ON public.invoice_reminders (client_id, created_at) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS invoice_reminders_client_created
    ON public.invoice_reminders (client_id, created_at);

DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['payment_settings', 'payment_methods', 'payments',
                           'invoice_reminders'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON public.%I'
      || ' USING (client_id = nullif(current_setting(''app.client_id'',true),'''')::uuid)'
      || ' WITH CHECK (client_id = nullif(current_setting(''app.client_id'',true),'''')::uuid)', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, csa_app, csa_ro, csa_identity', t);
  END LOOP;
END $$;
GRANT SELECT, INSERT ON public.payments TO csa_app;
GRANT SELECT, INSERT, UPDATE ON public.payment_settings, public.invoice_reminders TO csa_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.payment_methods TO csa_app;
