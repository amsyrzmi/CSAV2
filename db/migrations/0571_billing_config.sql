-- Billing configuration: the effective-dated tables credit pricing reads. Global (no client column),
--   append-only: a change is a new row with a later effective_from, never an edit, so a closed
--   cycle can always be priced again the same way.
-- Adds:
--   public.ai_model_prices (provider, model, input_usd_per_mtok, output_usd_per_mtok,
--     effective_from): USD per million tokens, keyed on the exact provider and model strings the
--     ledger records (ai_usage_events.provider and .model).
--   public.billing_fx_rates (usd_to_myr, effective_from).
--   public.billing_stage_fees (stage, monthly_fee_rm, effective_from).
--   public.billing_markup_tiers (stage, bundle, up_to_executions, markup_pct, effective_from):
--     up_to_executions NULL is the open-ended last tier.
--   Every table: CHECKs on the closed sets and on non-negative numbers, a unique key on the natural
--     key plus effective_from (a duplicate row is refused; for tiers NULL counts as one value),
--     and a BEFORE UPDATE OR DELETE trigger that raises, so even the owner role cannot edit or
--     remove history. TRUNCATE is deliberately left alone: test and staging resets use it.
--   csa_app gets SELECT and INSERT only (explicit REVOKE first, as 0570 does: the default
--     privileges would otherwise grant UPDATE and DELETE); csa_ro gets SELECT; nothing to PUBLIC,
--     csa_identity or csa_hr_maintenance. No row security: the tables hold no tenant data.
--   Seeds, all effective 2020-01-01 so any past usage can be priced:
--     fees: pilot RM99.99, collaborator RM119.99, terminated RM0 (owner, 2026-11-12);
--     markup tiers: 18 rows (pilot and collaborator x three bundles x three tiers, up to 2000
--       executions, up to 10000, open-ended), the owner's table of 2026-11-12;
--     prices and FX: sourced from the providers' public pages and BNM, cited beside each value.
--       Prices are keyed on the settings defaults GROQ_MODEL and GOOGLE_AI_MODEL (csa/core/
--       settings.py). A model set in the environment to anything else, and ASSISTANT_DRAFT_MODEL
--       when set, has no price here: its calls show as unpriced until a platform admin adds one.
-- No new roles and no FORCE ROW LEVEL SECURITY.
-- Pre-check: 0570 applied.
-- Rollback:
--   DROP TABLE public.ai_model_prices, public.billing_fx_rates, public.billing_stage_fees,
--     public.billing_markup_tiers;
--   DROP FUNCTION public.billing_config_append_only();
--   Finally delete the schema_migrations row for 0571, or the runner thinks it is still applied.

CREATE FUNCTION public.billing_config_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% % refused: the table is append-only, add a new row with a later effective_from',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TABLE public.ai_model_prices (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider text NOT NULL CHECK (provider <> ''),
    model text NOT NULL CHECK (model <> ''),
    input_usd_per_mtok numeric NOT NULL CHECK (input_usd_per_mtok >= 0),
    output_usd_per_mtok numeric NOT NULL CHECK (output_usd_per_mtok >= 0),
    effective_from timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, model, effective_from)
);

CREATE TABLE public.billing_fx_rates (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usd_to_myr numeric NOT NULL CHECK (usd_to_myr > 0),
    effective_from timestamptz NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.billing_stage_fees (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stage text NOT NULL CHECK (stage IN ('pilot', 'collaborator', 'terminated')),
    monthly_fee_rm numeric NOT NULL CHECK (monthly_fee_rm >= 0),
    effective_from timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (stage, effective_from)
);

CREATE TABLE public.billing_markup_tiers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stage text NOT NULL CHECK (stage IN ('pilot', 'collaborator', 'terminated')),
    bundle text NOT NULL CHECK (bundle IN ('individual', 'curated', 'complete')),
    up_to_executions int CHECK (up_to_executions > 0),
    markup_pct numeric NOT NULL CHECK (markup_pct >= 0),
    effective_from timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
-- NULL is the open-ended tier; it counts as one value, so two open-ended tiers cannot coexist.
CREATE UNIQUE INDEX billing_markup_tiers_natural_key ON public.billing_markup_tiers
    (stage, bundle, effective_from, (coalesce(up_to_executions, -1)));

CREATE TRIGGER ai_model_prices_append_only BEFORE UPDATE OR DELETE ON public.ai_model_prices
    FOR EACH ROW EXECUTE FUNCTION public.billing_config_append_only();
CREATE TRIGGER billing_fx_rates_append_only BEFORE UPDATE OR DELETE ON public.billing_fx_rates
    FOR EACH ROW EXECUTE FUNCTION public.billing_config_append_only();
CREATE TRIGGER billing_stage_fees_append_only BEFORE UPDATE OR DELETE ON public.billing_stage_fees
    FOR EACH ROW EXECUTE FUNCTION public.billing_config_append_only();
CREATE TRIGGER billing_markup_tiers_append_only BEFORE UPDATE OR DELETE ON public.billing_markup_tiers
    FOR EACH ROW EXECUTE FUNCTION public.billing_config_append_only();

REVOKE ALL ON public.ai_model_prices, public.billing_fx_rates, public.billing_stage_fees,
    public.billing_markup_tiers FROM PUBLIC, csa_app, csa_ro, csa_identity, csa_hr_maintenance;
GRANT SELECT, INSERT ON public.ai_model_prices, public.billing_fx_rates,
    public.billing_stage_fees, public.billing_markup_tiers TO csa_app;
GRANT SELECT ON public.ai_model_prices, public.billing_fx_rates, public.billing_stage_fees,
    public.billing_markup_tiers TO csa_ro;

-- Platform fee per month, RM. Owner's figures, 2026-11-12 (global constraints of the credit plan).
INSERT INTO public.billing_stage_fees (stage, monthly_fee_rm, effective_from) VALUES
    ('pilot', 99.99, '2020-01-01 00:00:00+00'),
    ('collaborator', 119.99, '2020-01-01 00:00:00+00'),
    ('terminated', 0, '2020-01-01 00:00:00+00');

-- Markup percent by stage, bundle and tier (executions in the cycle: up to 2000, up to 10000,
-- above). Owner's table, 2026-11-12. Terminated needs no rows: it pays no fee and no AI charge.
INSERT INTO public.billing_markup_tiers
    (stage, bundle, up_to_executions, markup_pct, effective_from)
SELECT t.stage, t.bundle, l.up_to_executions, t.pct[l.tier], '2020-01-01 00:00:00+00'
FROM (VALUES
    ('pilot', 'individual', ARRAY[75, 70, 65]::numeric[]),
    ('pilot', 'curated', ARRAY[70, 65, 65]::numeric[]),
    ('pilot', 'complete', ARRAY[67.5, 65, 65]::numeric[]),
    ('collaborator', 'individual', ARRAY[85, 80, 77.5]::numeric[]),
    ('collaborator', 'curated', ARRAY[80, 77.5, 77.5]::numeric[]),
    ('collaborator', 'complete', ARRAY[77.5, 77.5, 77.5]::numeric[])
) AS t(stage, bundle, pct)
CROSS JOIN (VALUES (1, 2000), (2, 10000), (3, NULL::int)) AS l(tier, up_to_executions);

-- Model prices, USD per million tokens, retrieved 2026-11-12. The model strings are exactly what
-- the provider clients send and the ledger records (GroqClient.model, GeminiClient.model).
-- groq / openai/gpt-oss-120b: input $0.15, output $0.60 per 1M tokens.
--   Source: https://console.groq.com/docs/model/openai/gpt-oss-120b (also listed at
--   https://console.groq.com/docs/models), retrieved 2026-11-12.
-- gemini / gemini-3.1-flash-lite: paid tier, standard, text input $0.25, output $1.50 per 1M
--   tokens, thinking tokens included in the output price (the Gemini client books candidate plus
--   thought tokens as completion tokens). Source: https://ai.google.dev/gemini-api/docs/pricing
--   (redirects to https://aistudio.google.com/docs/pricing), retrieved 2026-11-12.
INSERT INTO public.ai_model_prices
    (provider, model, input_usd_per_mtok, output_usd_per_mtok, effective_from) VALUES
    ('groq', 'openai/gpt-oss-120b', 0.15, 0.60, '2020-01-01 00:00:00+00'),
    ('gemini', 'gemini-3.1-flash-lite', 0.25, 1.50, '2020-01-01 00:00:00+00');

-- USD to MYR 4.0840. Source: Bank Negara Malaysia, Exchange Rates, Kuala Lumpur Interbank Foreign
--   Exchange Market, USD row for 12 Nov 2026 (https://www.bnm.gov.my/exchange-rates, retrieved
--   2026-11-12; the page does not label the rate type or session, so it is read as the interbank
--   middle rate). Back-dated to 2020-01-01 as a single rate so that all past usage can be priced;
--   a later platform-admin row (shadow rule: today or tomorrow) takes over from its date.
INSERT INTO public.billing_fx_rates (usd_to_myr, effective_from) VALUES
    (4.0840, '2020-01-01 00:00:00+00');
