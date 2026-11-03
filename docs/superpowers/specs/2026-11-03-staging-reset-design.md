# Staging reset: design

Status: draft for the owner's approval, 2026-11-03. Staging only. No migration and no product change.

## Why

Staging has grown from synthetic seeds and months of testing: demo tenants, demo accounts and old connections. The owner wants to retest the product the way a client would meet it, using real mailboxes and three companies that match how FourStacks sells: one serving customers and employees, one customers only, one employees only.

Nothing can do this today. `scripts/seed_staging.py` only adds, and `scripts/verify_reset_target.py` guards local development databases only.

## Goals

- One owner-run tool that wipes `csa_staging`, rebuilds it from the base schema and the migrations, and creates the agreed accounts and companies.
- A read-only plan first, which says what will be lost.
- A backup taken just before the wipe.
- Repeatable: the owner can reset staging again later with the same roster.
- Each company starts empty: accounts and packages only.

## Non-goals

- Production or any other environment, now or later. The tool has no setting that points it elsewhere.
- Sample data of any kind.
- Choosing HR owners. A person must have accepted their invitation and changed their password first, so this stays on the Team page.
- Reconnecting Telegram, WhatsApp or Gmail automatically. The steps are documented; the owner runs them.
- Any change to product behaviour, packages or the database schema, including the default for new companies. New companies still start in `shadow`; the tool sets the mode only for the companies its roster names.
- A "Go live" switch in the console. It is its own project, next after this one (see Owner decisions).

## Anti-goals

- A wipe that can run against anything except the isolated staging database.
- A wipe started by accident, by an agent or by a script without a person at a terminal.
- Real email addresses in this repository. **The repository is public.** The roster stays out of git, and tests use synthetic addresses.
- Secrets, passwords, invitation links or tokens in output, logs or files. Secret files under `/srv/csa-staging/secrets` are never read, printed, moved or changed by the tool itself; containers receive them through compose as they do today.
- Switching off the immutable-table triggers, or changing role passwords or `CREDENTIALS_KEY`.

## Owner decisions (2026-11-03)

1. Staging only.
2. The tool creates the companies, activates the packages and sends the invitations, rather than the owner doing it by hand.
3. **Option A**: rebuild the database inside the existing Postgres.
4. Companies start empty.
5. Company A is called **Team Jo**.
6. Packages:

| Company | Serves | Packages |
|---|---|---|
| Team Jo | Customers and employees | `customer_support`, `orders_products`, `subscriptions`, `bookings`, `hr`, `staff_email` |
| Kedai Mawar | Customers only | `customer_support`, `orders_products`, `subscriptions` |
| Syarikat Cekap | Employees only | `hr` |

The names Kedai Mawar and Syarikat Cekap were chosen for the owner, at their request, and can be renamed later.

7. **Replies.** A company in `shadow` mode sends customers no reply, and nothing in the console changes the mode. The tool sets Team Jo and Kedai Mawar to `auto`, so the owner can test what a customer really sees. The cost is that those bots answer anyone who messages them, using paid model calls. Syarikat Cekap stays in `shadow`, because it has no customers and no customer support package.
8. **Go live comes next.** The owner agreed that locking new clients by hand isn't practical at scale. New companies stay quiet until ready, but each tenant admin should be able to go live from the console, with a clear warning while replies are off. That is a separate small project straight after this reset and before selling.

## Approaches

- **A. Rebuild the database inside the existing Postgres (chosen).** Drop and recreate only `csa_staging`, empty the two upload volumes, leave the Postgres cluster alone, then load the base schema and run the existing migration gate. This is the narrowest wipe. The tool names one database, and `scripts/staging_migrate.py` already refuses any other target. Cluster roles survive, which is harmless because the gate resets their passwords from the secret files.
- **B. Delete the Docker volumes and start Postgres from nothing.** The most complete, and the same path as a new server. But the tool deletes volumes by name, so a mistake does more damage, and it adds a first-start path that rollouts never use, with nothing gained for these retests.
- **C. Empty the tables in place (ruled out).** The HR history and audit tables refuse `DELETE` and `TRUNCATE` by design, so this would mean switching those protections off. It would also leave old migration history behind.

## How it works today

- **Rollouts:** `scripts/staging_rollout.py` has plan and apply, a `Runner` that tests can fake, `compose_base()`, `build_env()`, `running_images()` and readable stop messages with recovery lines.
- **Base schema:** `scripts/migrate.py` applies only `db/migrations/*.sql`, which assume the extensions and base tables already exist. Those are in `db/schema_baseline.sql`, which contains psql meta-commands and so must go through `psql`. The Python image has the file but no psql. `deploy/RELEASE.md` (first install) takes the file from the Python image, pipes it to psql inside `staging-db`, and refuses a database that isn't empty.
- **Migrations:** the compose service `migrate` runs `scripts.staging_migrate` with `CSA_STAGING_MIGRATION_APPROVAL=approved-isolated-staging`. It requires `csa` on `staging-db:5432/csa_staging`.
- **First platform admin:** the compose service `admin-bootstrap` with `CSA_STAGING_ADMIN_BOOTSTRAP_APPROVAL`. It prompts with getpass and closes for good once one admin exists.
- **Further platform admins:** the compose service `admin-add` with `CSA_STAGING_ADMIN_ADD_APPROVAL`. It prompts for the approver's password and the new initial password. `add_platform_admin` **refuses an approver who still has to change their password**, so the first admin must sign in and change it before any other admin can be added.
- **Companies:** the route `POST /console/v1/admin/clients` in `csa/console/onboarding.py` runs `console/client_create.sql`, nine `console/provision_capabilities.sql` rows and one audit row. New companies get `runtime=langgraph` and deployment mode `shadow`, in which `csa/graph/policy/gate.py` rejects every turn with `shadow_mode`. The only code that changes the mode is `set_deployment_mode` in `csa/ops/runbooks.py`, using `ops/update_client_policy_mode.sql`; no console route calls it.
- **Packages:** `activate_package` in `csa/console/services.py` checks that the package is sellable and its requirements are active, switches on its capabilities and writes the audit row. `customer_support` must come first.
- **Invitations:** `_invite` in `csa/console/auth.py` calls `IdentityService.invite` with a notify callback that sends `invitation_mail`. An invitation lasts **48 hours** (`token_seconds`). An address that already has an account is refused.
- **Mail:** only the gateway has the SMTP secrets and outbound network. One-off containers on the internal `data` network can't send anything.
- **Telegram:** `scripts/staging_test_bot.py --tenant <slug> --test-bot --replace-webhook` binds the test bot, and refuses a bot bound to another tenant. The wipe removes the old binding, so the bot can be bound to Team Jo.

## The tool

`scripts/staging_reset.py`, run from the main checkout at `origin/main`, with three commands. It reuses the rollout script's helpers rather than copying them.

### `plan` (read-only; an agent may run it)

Prints and changes nothing else:

- The running image digests, and whether all four services run the image for HEAD.
- The current schema version and the migrations it will rebuild up to.
- Current counts: companies (with slugs), accounts, and files in each upload volume, so the owner can see what is about to go. Counts and slugs only, no content.
- The roster: companies, packages, and how many accounts of each role. Addresses are shown only in the terminal.
- What is lost and must be redone: every account and session, all HR and customer data, knowledge base files, Telegram, WhatsApp and Gmail connections.
- Free disk space against the size of the backup.

It stops with a clear reason if anything `wipe` would refuse is already true.

### `wipe` (owner only, at a terminal)

Refuses unless all of these hold:

- stdin is a real terminal;
- HEAD is on `origin/main`, the tracked tree is clean, and all services run the image for HEAD, so the rebuilt schema matches what the services expect;
- compose project `csa-staging`, database `csa_staging`, host `staging-db`, owner role `csa`, with these values fixed in code and not taken from arguments or the environment;
- the upload volumes are exactly `csa-staging_staging-uploads` and `csa-staging_staging-hr-uploads`;
- the roster file is present and valid;
- there is enough free disk;
- the owner types the exact phrase `wipe csa-staging` at the prompt. The prompt is not bypassable by a flag.

Then, in order:

1. **Back up** to `~/csa-staging-backups/<UTC timestamp>/`, directory mode 0700: a `pg_dump` of `csa_staging` and a tar of each upload volume. Stop if this fails; nothing has changed yet.
2. Stop gateway, frontend, worker and delivery.
3. In the running Postgres, run `DROP DATABASE csa_staging WITH (FORCE)` and `CREATE DATABASE csa_staging OWNER csa`, connected to the `postgres` maintenance database inside the staging database container.
4. Empty the two upload volumes through one-off containers with no network and no secrets, keeping the mount point and its ownership.
5. Load the base schema: confirm the new database has no tables, then pipe `db/schema_baseline.sql` from the running Python image to psql inside `staging-db`, stopping at the first error, as the first-install procedure does.
6. Run the `migrate` service and confirm that the schema version recorded matches `EXPECTED_SCHEMA_VERSION`.
7. Run `admin-bootstrap` for the first admin named in the roster. The owner types the initial password.
8. Recreate the services and wait for readiness.
9. Print the next step: sign in as that admin, change the password, then run `accounts`.

A failure from step 3 on prints the step, what state staging is in (for example, an empty database with no schema), and the recovery: fix the cause and run `wipe` again (it stays safe to repeat, because the database is about to be dropped anyway), or restore from the named backup. The restore commands are in the docs. A repeat takes a new backup.

### `accounts` (owner only, at a terminal)

1. Runs `admin-add` for each further platform admin in the roster. The owner types the first admin's password and an initial password for each. They hand those to the other admins privately; each must change it at first sign-in.
2. Asks for the first admin's password on the host with getpass, then runs the company step inside the running gateway, because only the gateway can send mail: `docker exec -i csa-staging-gateway-1 python -m scripts.staging_accounts`. The password and then the roster go in on stdin. Neither is mounted, or passed as arguments or environment.

The company step:

- Verifies the first admin's password from stdin, as `add_platform_admin` does, so nobody can run it from the socket alone. It then acts as that platform admin.
- Refuses unless invitation mail is actually on, built the way `apps/gateway/main.py` builds it (`Mailer.from_settings`, then `.enabled`). Otherwise an invitation link would have to be printed, which is not allowed.
- For each company: creates it with the same code as the console route, then activates its packages through `activate_package`, in dependency order, with reference `staging-reset`.
- Sets the deployment mode the roster names through `set_deployment_mode`, with one `console_audit` row naming the actor. A company whose roster entry names no mode is left in `shadow`.
- Invites each tenant member through `IdentityService.invite` with the same notify callback as the console, with no HR preset.
- Prints one line per item: `created`, `already there` or `mail not sent`, with addresses but never a link or token.

It is safe to run again. Existing companies, active packages and existing accounts are skipped, and no second email goes out. A slug that exists with a different name, or an address that exists in another company or with another role, stops with a clear message. An invitation that wasn't delivered or has expired is resent from the Team page by the existing resend path.

## Roster

A JSON file at `deploy/staging-accounts.local.json`, added to `.gitignore` in this change. `deploy/staging-accounts.example.json`, with `example.test` addresses and the three companies above, is committed as the shape.

It holds: the platform admins in order (the first is the bootstrap admin), and each company's slug, name, timezone, packages, deployment mode and members (email, display name, role).

The tool validates it strictly before doing anything: known fields, unique addresses, no address both platform admin and member, known and sellable package keys, roles limited to `tenant_admin`, `tenant_staff` and `read_only`, and at least one `tenant_admin` per company, and a deployment mode of `shadow`, `suggest` or `auto`. This is why it is not in a committed file: the addresses are personal data and the repository is public.

The roster has three platform admins and seven members, ten accounts in all. Slugs: `team-jo`, `kedai-mawar` and `syarikat-cekap`.

Team Jo's roles: tenant admin `tenant_admin`; HR owner, manager and staff `tenant_staff`; read-only `read_only`. Kedai Mawar and Syarikat Cekap each have one `tenant_admin`. The first platform admin is the owner's fourstacks.my account, with the Gmail account last as the emergency login. These are defaults for the owner to check.

The display name for each role account is the role, such as "Team Jo HR Owner". People can change their own names later.

## After the tool

The docs give this order, in `deploy/STAGING_PIPELINE.md`, as a new section "Resetting staging":

1. Each invited person accepts within 48 hours and sets a password.
2. Team Jo's tenant admin sets the HR owner on the Team page once that person has signed in. Syarikat Cekap's tenant admin makes themselves the HR owner.
3. Team Jo's HR owner gives the manager and staff accounts their HR access on the access page. Invitations carry no HR preset, so until then those two accounts look the same and have no HR rights. A platform admin can't choose the manager preset anyway; only the HR owner can.
4. Team Jo's tenant admin turns on the email assistant's inner switch, which only a tenant admin of the company can change. Activating the Staff email package doesn't do it.
5. Bind the test bot: section 5 with `--tenant team-jo`.
6. Connect WhatsApp and Gmail again when wanted. Google test users stay as they are, since they live in Google Cloud.
7. Do the pending retests in `docs/ASSISTANT_RETEST.md`.

Also in this change:

- Section 11 of `deploy/STAGING_PIPELINE.md` says the Google client is Internal. It is now External, in testing: listed test users only, at most 100, with access expiring every 7 days.
- `deploy/STAGING_SEED.md` gets a line saying the seed isn't part of a reset, and that seeding after one would add demo tenants beside the real ones.
- `docs/PROGRESS.md` gets its entry.

## Constraints

- Synthetic fixtures and disposable testcontainers only. Tests never send mail, never contact Telegram or Google, and never touch the staging containers.
- Never connect to production, its bot tokens or n8n.
- Never FORCE RLS. No new roles and no migration.
- `scripts/staging_accounts.py` must be in the image, so the order is: PR, CI, publish, normal scripted rollout (no migrations), then the owner runs the tool. `staging_reset.py` itself runs from the checkout.
- The host is shared and short of memory, and the disk is 88% full (12 GB free). The backup is small today (database about 100 MB), but the tool checks the space rather than assuming.
- CI is the merge gate. Locally, run only the changed files' tests.
- `lib/navigation.ts` and `components/AppShell.tsx` stay untouched. No console change is expected at all.

## What must be true when it's done

- `plan` changes nothing, proven by a test with a fake runner that records every command.
- Every `wipe` refusal above is tested, and each one proves that no destructive command ran.
- A wrong confirmation phrase, a non-terminal stdin, and arguments that try to name another project or database are all refused.
- On a disposable Postgres: the drop, create, base schema and migrate sequence ends at `EXPECTED_SCHEMA_VERSION`, while the cluster roles still work with the passwords the gate sets.
- On a disposable migrated database with a fake mailer: the company step creates the synthetic roster's companies with exactly the agreed packages active and their capabilities on, sets the named deployment modes and leaves the others in `shadow`, sends one invitation per member, prints no token, and a second run changes nothing and sends nothing.
- With mail off, the company step refuses before creating anything.
- The prompts that need a real terminal (`admin-bootstrap`, `admin-add`, the confirmation) can't be covered by the fake runner. The tests cover everything around them, and they are checked by the real run.
- A backup restores: tested once by hand or in a disposable container, with the commands in the docs matching what was run.
- After the real reset on staging: signing in works for all ten accounts; each company sees only its own package tabs; Team Jo's tenant admin can make the HR owner on the Team page; and a Telegram message to the bot reaches Team Jo's Inbox and gets a reply from the bot after the bot is bound.

## Left to the builder

- How the company step shares code with the onboarding route: extracting one function is preferred over copying again, as long as the route's behaviour doesn't change.
- How the empty step of the upload volumes is done, as long as it has no network and no secrets, and the volumes aren't deleted.
- The exact backup format and the disk check's margin.
- Which connection `set_deployment_mode` uses inside the gateway, as long as no new role is added and RLS isn't weakened. If the app role can't make this update, raise it rather than widening a grant quietly.
- How `staging_reset.py` passes a real terminal through to the prompts, since the rollout script's `Runner` captures output.
