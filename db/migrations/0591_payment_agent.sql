-- Payments 1, agent and sign-up email: the `payment_agent` capability and signup_requests.email.
-- Adds:
--   One capability_registry row `payment_agent` for every company ('Payments', pattern python,
--     workflow python:payment_agent, input_schema '{}': the code's Input class syncs it). Its
--     is_enabled and client_enabled are copied from that company's subscription_agent row, and are
--     false when it has none, so the bot answers about payments only where it already handles
--     subscriptions. A company created later gets its row from onboarding
--     (csa/console/onboarding.py). ON CONFLICT DO NOTHING keeps a row a company already has.
--   signup_requests.email: nullable, 3 to 254 characters and lower case when set. The address a
--     customer gave in the chat for payment reminders; it is a claim nobody has checked and is kept
--     apart from contacts.email, as contacts.customer_given_email (0512) is.
-- No new tables, roles or policies. Safe to run twice: IF NOT EXISTS, the check is looked up by name,
--   the capability rows ON CONFLICT DO NOTHING.
-- Pre-check: 0590 applied; capability_registry (baseline, unique (client_id, name)), clients and
--   signup_requests (0580) exist. Both statements are cheap: signup_requests is small and the new
--   column is nullable.
-- Rollback (as the database owner, with the services stopped), in this order:
--   DELETE FROM public.capability_registry WHERE name = 'payment_agent'; (a company's own switch on
--   it is lost);
--   ALTER TABLE public.signup_requests DROP CONSTRAINT signup_requests_email_chk, DROP COLUMN email;
--   (emails given at sign-up are lost). Finally delete the schema_migrations row for 0591, or the
--   runner thinks it is still applied.

DO $$
BEGIN
  IF to_regclass('public.capability_registry') IS NULL
     OR to_regclass('public.signup_requests') IS NULL
     OR to_regclass('public.payments') IS NULL THEN
    RAISE EXCEPTION '0591: needs capability_registry, signup_requests (0580) and payments (0590)';
  END IF;
END $$;

INSERT INTO public.capability_registry (
    client_id, name, display_name, description, pattern, tier, version,
    risk_class, input_schema, is_enabled, client_enabled, workflow_id
)
SELECT c.id, 'payment_agent', 'Payments', 'Invoice payment status and how to pay', 'python',
       'standard', '1.0', 'low', '{}'::jsonb,
       coalesce(s.is_enabled, false), coalesce(s.client_enabled, false), 'python:payment_agent'
  FROM public.clients c
  LEFT JOIN public.capability_registry s
         ON s.client_id = c.id AND s.name = 'subscription_agent'
ON CONFLICT (client_id, name) DO NOTHING;

ALTER TABLE public.signup_requests ADD COLUMN IF NOT EXISTS email text;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                  WHERE conrelid = 'public.signup_requests'::regclass
                    AND conname = 'signup_requests_email_chk') THEN
    ALTER TABLE public.signup_requests
        ADD CONSTRAINT signup_requests_email_chk
        CHECK (email IS NULL OR (char_length(email) BETWEEN 3 AND 254 AND email = lower(email)))
        NOT VALID;
  END IF;
  ALTER TABLE public.signup_requests VALIDATE CONSTRAINT signup_requests_email_chk;
END $$;
