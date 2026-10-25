# Staging seed: synthetic tenants, roles and records

Status: owner-approved additive seed for the isolated staging database
(`csa_staging`). It supersedes the earlier files-only manual testing pack for
this purpose. It never resets, deletes or re-bootstraps anything, and it does
not start workers, call providers, register bots, create KB vectors or
fabricate deliveries, approvals or runtime traces.

## What it creates

| Area | Records |
| --- | --- |
| Tenants | `demo-meranti` (capabilities enabled) and `demo-foreign` (disabled), onboarded with the console onboarding SQL, nine capability rows each and an attributed `tenant.created` audit entry |
| Console roles | Meranti: `tenant_admin` (admin and independent reviewer), `tenant_staff` (manager and support), `read_only`; Foreign: one `tenant_admin`. Created by invite plus accept through `IdentityService` as the existing platform administrator, so identity audit is real |
| Passwords | Generated per account, printed once by the seed, and every account is forced to change it at first login. Sessions created during acceptance are revoked |
| HR | Meranti admin becomes HR client owner via the HR owner bootstrap SQL. Three employees (manager, support linked to the staff account, finance reviewer) and role-appropriate grants are applied through `public.hr_admin_apply`, so `hr_operations` receipts and `hr_authority_events` are genuine. Payroll preparer (admin) and reviewer are different people; the reader has no HR access |
| Business records | 8 Meranti products plus one foreign marker, 3 contacts, 3 Telegram-labelled conversations, 4 historical messages, orders `MGL-TEST-001` to `008` covering every status, 8 charger line items, one foreign order, 2 tickets, 2 invoices, 2 knowledge gaps |

Service packages: for a capability-enabled tenant (`demo-meranti`) the seed also
writes `active` rows in `console_service_packages` (reference `staging-seed-v1`,
activated by the seeding platform administrator) for every package whose
capabilities it enables: `customer_support`, `orders_products`, `subscriptions`,
`bookings` and `marketplaces`. It activates `hr` for every tenant it assigns an
HR owner (`demo-meranti`), in the same transaction as the HR step.
`e_invoicing` is not activated because onboarding provisions no `lhdn_agent`
row, and `staff_email` is not activated because the seed never configures
`email_settings`; activate them from the Services tab. `demo-foreign` gets no package rows.

Not created: KB documents or vectors (prod requires TEI and the ingestion
worker), outbox or deliveries, bot credentials or endpoints, approvals,
marketplace grants, subscriptions, HR cases, candidates, leave or payroll data.
Those still need the supported UI paths or the pipeline activation.

## Guards

- `CSA_STAGING_SEED_APPROVAL=approved-isolated-staging` is required before any
  secret file is read.
- Owner connection must be `csa` on `staging-db:5432/csa_staging`; identity
  connection must be `csa_identity` on the same database. Anything else is
  refused before connecting.
- An advisory lock serialises concurrent runs. Re-running is safe: existing
  tenants, accounts, HR owner, employees and records are detected and skipped;
  grants are re-applied idempotently.
- A tenant slug that already exists with a different id, an account that
  exists without the expected membership, or an HR owner that is someone else
  stops the seed with an explicit message.

Validation: `tests/test_seed_staging.py` runs the whole seed twice against a
disposable migrated Testcontainer and checks roles, forced password change,
revoked sessions, HR receipts and exact record counts.

## Running against staging (separate approval)

Run from an approved checkout as a one-off container on the accepted Python
image. Only the script and its SQL directory are mounted over the image; the
owner and identity connection strings are mounted as read-only secret files.

```bash
(
  cd /home/amsyrzmi/CSAV2-seed || exit 1
  docker run --rm --network csa-staging_data --user 1000:1000 \
    --env NO_LOCAL_ENV=1 \
    --env CSA_STAGING_SEED_APPROVAL=approved-isolated-staging \
    --volume /srv/csa-staging/secrets/migration/staging_owner_dsn:/run/secrets/STAGING_OWNER_DSN:ro \
    --volume /srv/csa-staging/secrets/runtime/staging_identity_dsn:/run/secrets/IDENTITY_DATABASE_URL:ro \
    --volume "$PWD/scripts/seed_staging.py:/app/scripts/seed_staging.py:ro" \
    --volume "$PWD/db/sql/seed_staging:/app/db/sql/seed_staging:ro" \
    ghcr.io/amsyrzmi/csa-python@sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764 \
    python -m scripts.seed_staging
)
```

The container joins only the internal `data` network; it has no egress. The
output lists each account with its one-time password. Hand those over
privately and do not paste them into documents or chat logs. First login
forces a password change.

Afterwards, record the printed counts in PROGRESS and verify in the console:
tenant selector shows both demo tenants, Meranti members list five roles,
HR directory shows three employees, and the catalogue and orders pages show
the synthetic records. The foreign tenant must never appear while signed in
to Meranti.
