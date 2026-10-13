-- Keep the two non-production sandbox tenants available after make db-reset.
INSERT INTO public.clients (
    id,
    slug,
    name,
    tier,
    status,
    platform_version,
    timezone,
    region,
    config,
    brand_name,
    languages,
    core_object,
    deployment_mode,
    tokens_per_day,
    onboarded_at,
    created_at,
    updated_at
)
VALUES
    (
        '11111111-1111-1111-1111-111111111111',
        'sandbox-amsyar',
        'Sandbox Client - Amsyar',
        'growth',
        'pilot',
        '1.0.0',
        'Asia/Kuala_Lumpur',
        'MY',
        '{"quote_tax_rate": 0, "quote_tax_label": "Tax", "quote_auto_limit": 5000, "quote_valid_days": 7}'::jsonb,
        'Sandbox A',
        ARRAY['en', 'ms', 'zh', 'ta'],
        'order',
        'shadow',
        400000,
        '2026-09-23 08:14:31.909552+00',
        '2026-09-23 08:14:31.909552+00',
        '2026-09-23 08:14:31.909552+00'
    ),
    (
        '22222222-2222-2222-2222-222222222222',
        'sandbox-ihsan',
        'Sandbox Client - Ihsan',
        'growth',
        'pilot',
        '1.0.0',
        'Asia/Kuala_Lumpur',
        'MY',
        '{"quote_tax_rate": 0, "quote_tax_label": "Tax", "quote_auto_limit": 5000, "quote_valid_days": 7}'::jsonb,
        'Sandbox B',
        ARRAY['en'],
        'order',
        'shadow',
        400000,
        '2026-09-23 08:14:31.909552+00',
        '2026-09-23 08:14:31.909552+00',
        '2026-09-23 08:14:31.909552+00'
    );

SELECT public.seed_core_capabilities(id) FROM public.clients WHERE slug LIKE 'sandbox-%';
