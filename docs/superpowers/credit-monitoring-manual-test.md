# Credit monitoring: manual test guide

Branch `feat/credit-monitoring`. This guide walks the Credit tab (client Admins) and the Billing
page (platform admins) on a local stack with synthetic data only. Spec:
[2026-11-10-credit-monitoring-design.md](./specs/2026-11-10-credit-monitoring-design.md).

The default path below was run end to end on 2026-11-13 (KL date), with the results quoted in
"What you should see". Every name, email and password here is synthetic; use no real customer
data, no production or staging keys, and no production database.

> **Warning: do not run plain `make db-reset`, `make db-seed`, `make up` or `make down` for this
> test.** Without `DEV_PGPORT=...` they act on the shared `csa_dev_db` container
> (`127.0.0.1:5434`), which other sessions and worktrees on this host use: `make db-reset` drops
> its `public` and `lg` schemas. The steps below use a throwaway container of their own on
> `127.0.0.1:5444` instead, and leave `csa_dev_db` alone.

## 1. Start an isolated stack

Run every command from the worktree root (`.claude/worktrees/feat-credit-monitoring`) unless a
step says otherwise. Use three terminals: one for setup, one for the gateway, one for the web
console.

1. Check that the database image is there (build it with `make db-image` only if this fails):

   ```bash
   docker image inspect csa/pg16-postgis-vector:1 --format '{{.Id}}'
   ```

2. Check that ports 5444, 18090 and 3010 are free (no output means free):

   ```bash
   ss -ltn | grep -E ':(5444|18090|3010)\b'
   ```

3. Start the throwaway Postgres:

   ```bash
   docker run -d --name csa-credit-demo-db --memory 512m \
     -e POSTGRES_USER=csa -e POSTGRES_PASSWORD=dev_owner_pw -e POSTGRES_DB=csa \
     -p 127.0.0.1:5444:5432 csa/pg16-postgis-vector:1
   docker exec csa-credit-demo-db pg_isready -U csa -d csa
   ```

4. This host has no `psql`, which `make db-reset` needs. Make a stand-in that runs `psql` inside
   the throwaway container. `scripts.verify_reset_target` still compares this `psql`'s database
   identity with the Python DSN's before anything is dropped, so a wrong port or container is
   refused.

   ```bash
   mkdir -p /tmp/csa-credit-demo/bin
   cat > /tmp/csa-credit-demo/bin/psql <<'EOF'
   #!/usr/bin/env bash
   # psql stand-in: runs psql inside csa-credit-demo-db; drops the postgres:// argument.
   set -euo pipefail
   args=()
   for a in "$@"; do
     case "$a" in postgres://*|postgresql://*) ;; *) args+=("$a") ;; esac
   done
   exec docker exec -i csa-credit-demo-db psql -U csa -d csa "${args[@]}"
   EOF
   chmod +x /tmp/csa-credit-demo/bin/psql
   ```

5. Reset the throwaway database (baseline, migrations up to 0572, the sandbox seed, and a login
   for the identity role). `DEV_PGPORT=5444` on the command line is what points it at the new
   container; `NO_LOCAL_ENV=1` keeps any `.env.local` (which points at 5434) out of it. Do not pipe
   this into `head`: a closed pipe stops the reset half way.

   ```bash
   PATH=/tmp/csa-credit-demo/bin:$PATH NO_LOCAL_ENV=1 \
     CSA_IDENTITY_PASSWORD=synthetic-identity-pw-5444 \
     make db-reset DEV_PGPORT=5444
   ```

   The first line must read `Verified psql and Python target the same isolated local dev
   database.` and the last `>> db-reset complete`.

6. Write the demo environment. It points the gateway and the seed at 5444 only, and needs no
   `.env.local`:

   ```bash
   cat > /tmp/csa-credit-demo/demo.env <<'EOF'
   NO_LOCAL_ENV=1
   ENV=dev
   SEED_DATABASE_URL=postgres://csa:dev_owner_pw@127.0.0.1:5444/csa
   DATABASE_URL=postgresql://csa_app:dev_app_pw@127.0.0.1:5444/csa
   LG_DATABASE_URL=postgresql://csa_app:dev_app_pw@127.0.0.1:5444/csa
   IDENTITY_DATABASE_URL=postgresql://csa_identity:synthetic-identity-pw-5444@127.0.0.1:5444/csa
   CONSOLE_ALLOWED_ORIGIN=http://127.0.0.1:3010
   CONSOLE_COOKIE_SECURE=false
   PUBLIC_BASE_URL=http://127.0.0.1:3010
   GROQ_API_KEY=synthetic-not-a-real-key
   CREDENTIALS_KEY=base64:c3ludGhldGljLWNyZWRlbnRpYWxzLWtleS0zMmJ5dGU=
   TURN_ENGINE=echo
   OTEL_EXPORTER=console
   KB_INGEST_ENABLED=false
   EOF
   ```

   In the setup terminal and the gateway terminal, load it:

   ```bash
   set -a; . /tmp/csa-credit-demo/demo.env; set +a
   ```

   These terminals now carry `ENV=dev` and the 5444 DSNs; close them when you are done.

7. Create the first platform admin. It prompts for a password (use a synthetic one, for example
   `synthetic-initial-password-123`); the first sign-in asks you to change it.

   ```bash
   uv run python -m scripts.bootstrap_console_admin \
     --create-first-platform-admin --email admin@example.test --display-name 'Synthetic Admin'
   ```

8. Seed the credit demo, then run it again to see that it adds nothing:

   ```bash
   uv run python -m scripts.seed_credit_demo
   uv run python -m scripts.seed_credit_demo
   ```

   The first run prints `usage_rows_added` of about 330 and `plans_added: 2`; the second prints
   `usage_rows_added: 0`, `plans_added: 0` and two `already has a plan row` lines. The seed refuses
   any DSN that is not a loopback port other than 5432/5433 with user and database `csa`, and it
   needs the platform admin from step 7 (plan rows are audited under a platform admin).

9. Start the gateway (gateway terminal, demo env loaded). `make run-dev` is not used here: it needs
   `.env.local`, loads it (5434), and also starts the worker, delivery and Telegram processes,
   none of which the Credit tab needs.

   ```bash
   uv run uvicorn apps.gateway.main:app --host 127.0.0.1 --port 18090
   ```

   Check it from the setup terminal: `curl -s http://127.0.0.1:18090/healthz` answers 200.

10. Build and start the web console (third terminal, from `CSA_WebConsole/csa-console/web`). This
    host has no npm; `node` is the Playwright one in the worktree's `.venv`. The `/console/v1`
    rewrite is fixed at build time, so build with the gateway's origin:

    ```bash
    cd CSA_WebConsole/csa-console/web
    NODE=../../../.venv/lib/python3.12/site-packages/playwright/driver/node
    CSA_CONSOLE_API_ORIGIN=http://127.0.0.1:18090 NEXT_TELEMETRY_DISABLED=1 \
      $NODE node_modules/next/dist/bin/next build
    CSA_CONSOLE_API_ORIGIN=http://127.0.0.1:18090 NEXT_TELEMETRY_DISABLED=1 \
      $NODE node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3010
    ```

    `next start` warns that it "does not work with output: standalone"; it serves the pages
    correctly for this test. Open http://127.0.0.1:3010/login.

## 2. Sign in

- **Platform admin.** Sign in as `admin@example.test` with the step 7 password, and set a new
  synthetic one (for example `synthetic-admin-password-456`).
- **Client Admin for Sandbox Client - Amsyar** (the demo company). As the platform admin open
  Settings, Team (`/settings/team`), choose "Sandbox Client - Amsyar", press "Invite someone",
  give a synthetic name and `owner-a@example.test`, role Admin, and send. Mail is off, so the
  invite link is shown on the page: open it in a private window and set a synthetic password
  (for example `synthetic-tenant-admin-789`). You land in the portal as that company's Admin.
- **Client Admin for Sandbox Client - Ihsan.** The same with `owner-b@example.test`.
- **A staff member** (to check the refusal): invite `staff-a@example.test` to the demo company
  with the Staff role.

## 3. What the seed holds

- **Sandbox Client - Amsyar:** pilot / Individual, joined three days after the current cycle's
  start (or today if that is earlier); 2026-11-08 on the run above. About 330 ledger rows in about
  260 executions: 110 in the previous cycle (before the plan, so not billed) and the rest in the
  current one. All seven jobs, Groq and Gemini, one three-call customer reply, one Groq-to-Gemini
  fallback, one execution that starts three minutes before the current cycle and ends two minutes
  after it (it belongs to the previous cycle), and one assistant request whose second call used
  `openai/gpt-oss-20b`, which has no price yet.
- **Sandbox Client - Ihsan:** collaborator / Complete from the current cycle's start, no usage.
- Executions between the cycle start and the join date are not billed; the admin overview counts
  them in `usage_without_plan` and shows them as "Usage before the join date: N executions, not
  billed".
- The seed places its rows relative to the day it is run (the join date, the spread of executions
  and the cut-off ten minutes before now all move with it), so the execution counts, RM amounts
  and the "27 / 30 days" proration below are those of the 2026-11-13 run: your numbers will differ.
  Check that the figures agree with each other (cards, table and charts), not that they
  match these.
- Tier stepping (above 2,000 and above 10,000 executions in a cycle) is not exercised by the seed,
  which stays far below 2,000; it is covered by the Task 7 unit vectors
  (`tests/test_billing_pricing.py`).

## 4. What each Credit-tab state looks like

Open Credit (`/portal/credit`) as the company's Admin. Figures are those of the 2026-11-13 run;
they depend on the date the seed is run (section 3), so yours will differ. Owner decision
2026-11-13: the bill line is called "AI Usage Charges" and the client sees the bill, the charges
per AI agent and per day and the execution count, but no list of single executions, no model and
no provider.

- **Pending prices** (Amsyar, current cycle, before checklist item 9.5): the banner "Bill so far,
  final on 5 Dec", the line "Billed from 8 Nov", and the amber notice "Some usage is still being
  priced. It is added to the AI Usage Charges once it is priced." Cards: Executions 101, AI Usage
  Charges RM 0.66, Platform fee RM 89.99 (RM 99.99 x 27 / 30 days), Bill so far RM 90.65. In the
  per-agent table the Workspace assistant's charges leave out the usage still being priced; its
  executions count still includes that execution.
- **Normal** (Amsyar, after the price is added): no amber notice; AI Usage Charges RM 0.67, bill
  so far RM 90.66. The per-agent charges and the daily charges each add up to the AI Usage Charges
  exactly (4 places in the table and charts, 2 on the cards).
- **Not set up** (Amsyar, previous cycle "5 Oct to 5 Nov, billing not set up" in the picker; or any
  company with no plan): only "Billing not set up yet. Your AI Usage Charges and bill will show
  here once your plan is set up." No cards, no RM 0.00 figures.
- **Zero usage** (Ihsan): cards Executions 0, AI Usage Charges RM 0.00, Platform fee RM 119.99,
  Bill so far RM 119.99; "No AI Usage Charges in this cycle yet." under By AI agent. No charts.
- **Terminated** (Ihsan after checklist item 9.7): in the billing cycle that contains the termination
  date, the Credit tab shows this notice above the cards: "Your plan ended on <date>. The figures below
  are the final AI Usage Charges and bill for your last billing period." The cards, agent table and
  charts show that last period, billed from the cycle start (or join date) up to the termination day. A
  termination dated the current cycle's start (the join date here) leaves nothing billed: every card
  RM 0.00 or 0, with the notice still shown. A termination dated later in the cycle shows "Billed to
  <date>" and a prorated fee. Pick any later cycle (the picker still lists it, at RM 0.00) and the page
  shows only "Your plan has ended.", with no cards, table or charts.
- **Staff or read-only member:** no Credit link in the rail; `/portal/credit` is a 404 page and
  the two `/console/v1/billing/usage*` routes answer 403.

## 5. Checklist

1. **Cards** (Amsyar): the four cards match section 4; "Bill so far" while the cycle is open.
2. **Per-agent table**: five rows with avatars (Training summary has the neutral bot icon):
   Customer support assistant, Email assistant, Workspace assistant, Training summary, HR assistant,
   highest charges first, each with its executions and its AI Usage Charges at 4 places. The
   executions column adds up to the Executions card (101).
3. **No per-execution list** (owner decision 2026-11-13): there is no executions list and no "Load
   more" button anywhere on the page, and no model, provider or job name is shown. The words "AI
   usage price" appear nowhere; the bill line reads "AI Usage Charges".
4. **Both charts**: "AI Usage Charges per day" (bars from the join date to today, every day of the
   cycle on the axis, "Highest day: ..." above it) and "AI Usage Charges by AI agent" (horizontal
   bars with the charges beside each).
5. **Cycle picker**: "5 Nov to 5 Dec (open), bill so far RM 90.65" and "5 Oct to 5 Nov, billing not
   set up"; switching shows the not-set-up state.
6. **Not set up**: as above, for the previous cycle and for a new company.
7. **Pending prices, then priced**: add the missing price on `/billing` (checklist item 9.5), reload
   Credit: the notice is gone and the AI Usage Charges include the newly priced usage.
8. **No forbidden field reaches the client.** In the browser's developer tools, Network tab, with
   the Credit tab open as the client Admin, look at every `/console/v1/billing/usage` and
   `/usage/cycles` response: none contains a token count, raw price or cost, markup, margin, USD or
   AWS figure, model or provider name (search the responses for `token`, `raw`, `cost`, `markup`,
   `margin`, `usd`, `aws`, `prompt`, `completion`, `model`, `provider`, `groq`, `gemini`). Only
   `/auth/csrf` and `/auth/login` mention `token` (the CSRF token), which is expected. The Credit
   tab makes no `/usage/executions` request, and opening
   `/console/v1/billing/usage/executions` in the same browser tab answers 404 (the route no longer
   exists).
9. **Platform-admin Billing page** (`/billing`, platform admin):
   1. Overview of the open cycle: one row per company with plan, executions, tokens in and out,
      raw cost, AI charge, platform fee, billed total and AI margin. Amsyar shows "Usage before the
      join date: N executions, not billed" (the executions between the cycle start and the join
      date) and "Some usage has no price yet: 1 call in 1 execution". The cycle picker shows past
      cycles; the previous cycle shows "Has usage but no billing plan yet: 111 executions, not
      billed" for Amsyar (counts from the 2026-11-13 run).
   2. Plan editor: "Billing plan" on a company row opens it on the open cycle only (it is hidden on
      a closed cycle).
   3. Price form, shadow rule: Groq, `openai/gpt-oss-120b`, 0.15 / 0.60, dated a few days before
      today (inside the priced range) is refused with "A price can only be back-dated before the
      earliest existing price for that model, to fill unpriced calls; otherwise it must start today
      or tomorrow. Pick another start."
   4. FX form, shadow rule: 4.0840 dated a few days ago is refused with the matching rate sentence.
   5. Missing price: Groq, `openai/gpt-oss-20b`, input 0.10, output 0.50 (demo figures, not the
      provider's price), dated the previous cycle's start (5 Oct here), 00:00. It is accepted ("Price
      saved for Groq openai/gpt-oss-20b from 5 Oct 2026, 00:00 Kuala Lumpur time."), because the
      model has no earlier row; the pending warning goes and Amsyar's status becomes ok.
   6. "Check against Langfuse" on a company: without Langfuse keys it answers "Langfuse not
      reachable" and "Nothing was compared, and the bill is not affected. Try again later." With a
      project configured, see section 6.
   7. Terminate Ihsan: in its plan editor choose Terminated, date the current cycle's start, give a
      reason, save. Termination is final in that database (reset the container to undo). Ihsan's
      Credit tab then shows the terminated state above.
10. **Tenants page** (`/settings/clients`): choose a company. "Billing plan" shows "Now: Pilot,
    Individual" (Amsyar) or "Now: Not set up" (a new company, form pre-filled Pilot / Individual for
    a first plan). While the open-cycle overview has not loaded, or if it fails, the plan is unknown
    and both Stage and Bundle start on an empty "Choose…" that must be changed before saving.
11. **Access**: a client Admin asking for another company (`?client_id=` of the other sandbox) or
    for `/console/v1/billing/admin/overview` gets 403.

## 6. Pointing "Check against Langfuse" at a Langfuse project

Use a Langfuse **test** project, never the production project or keys. Add to
`/tmp/csa-credit-demo/demo.env`, reload it in the gateway terminal and restart the gateway:

```bash
LANGFUSE_BASE_URL=https://jp.cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=pk-lf-<test project>
LANGFUSE_SECRET_KEY=sk-lf-<test project>
```

The seed writes only our ledger, never Langfuse, so for seeded companies the check reports a
difference (Langfuse has nothing). A "match" needs real calls made through the gateway with those
keys set (section 7). The Hobby plan limits the Metrics API to 100 requests a day; a 429 shows as
"Langfuse not reachable".

**Ids in Langfuse (owner decision 2026-11-13).** Langfuse receives the client's UUID as its user
id and the conversation's UUID as its session id, so a company's traces are found in Langfuse by
filtering on its client id. Node summaries are still scrubbed: free text, email addresses, phone
numbers and other UUIDs in them show as `[redacted]`.

## 7. Owner-only live checks

These need real provider and Langfuse accounts, so they could not be run here. Use test keys and a
Langfuse test project only, on this isolated stack (or staging), never production.

1. **Usage field names.** Groq: set a real test `GROQ_API_KEY` in the demo env, set
   `WORKSPACE_ASSISTANT_ENABLED=true` (or use the Training summary or Email path), restart the
   gateway and make one request. Gemini is only built in the worker, as the customer turn's compose
   fallback (`apps/worker/main.py`, needs `GOOGLE_AI_API_KEY` and `TURN_ENGINE=coordinator`), so the
   easiest real Gemini row comes from a staging customer conversation whose compose step falls back,
   or from a worker run here with a channel set up. Then compare the ledger rows with the
   providers' usage:

   ```bash
   docker exec csa-credit-demo-db psql -U csa -d csa -c \
     "select occurred_at, job, provider, model, prompt_tokens, completion_tokens, schema_name
      from ai_usage_events order by occurred_at desc limit 5"
   ```

   Groq rows must carry `usage.prompt_tokens` / `usage.completion_tokens`; Gemini rows
   `usageMetadata.promptTokenCount` and `candidatesTokenCount` plus `thoughtsTokenCount` as
   completion. A row with zero completion tokens on a real reply means a field name is wrong.
2. **Langfuse attributes.** In the Langfuse test project, find the generations for those calls:
   user id = the company's client id (its UUID), session id = the conversation UUID where there is
   one, trace name = the job (for example `workspace_assistant`),
   and the agent and job tags; model and input/output usage filled; no prompt or reply text. Then
   press "Check against Langfuse" for that company and cycle: it should say "Matches Langfuse".
   Check that the Metrics API's `inputTokens` / `outputTokens` (`/api/public/v2/metrics`) read back
   the usage we sent, and note the number format of the sums (integer, float, or string) in the
   check's response (`langfuse_input_tokens` / `langfuse_output_tokens`).
3. **Retention.** Check whether the Metrics API honours the plan's data-retention window (Hobby
   30 days, Core 90 days). If it does, a closed cycle older than the window shows a false "Does not
   match Langfuse"; record it so a note or a look-back limit can be added.
4. **`toTimestamp` inclusivity.** Make one call, then run the check with the cycle boundary at
   that call's exact time if you can (or read Langfuse's answer for a window ending exactly on a
   generation's timestamp) to see whether `toTimestamp` is inclusive. The check stops 5 minutes
   before now on both sides for the open cycle.

5. **Model prices before go-live.** In each environment (staging, then production), compare the
   models the processes are configured to call with the priced models. An unpriced model shows as
   `pending_prices`, and its executions are left out of the AI charge until a price is added. Read
   the configured models (unset means the settings default in `csa/core/settings.py`):

   ```bash
   grep -E '^(GROQ_MODEL|GOOGLE_AI_MODEL|ASSISTANT_DRAFT_MODEL)=' <that environment's env file>
   ```

   and the price rows (on a host of that environment, with its `DATABASE_URL` exported):

   ```bash
   psql "$DATABASE_URL" -c \
     "select provider, model, effective_from from ai_model_prices order by provider, model, effective_from"
   ```

   Add a row for every configured model that has none, before the first billed cycle, on
   `/billing` (price form). If usage of that model already exists in the open cycle, date the price
   at the cycle's start (allowed while the model has no earlier row), so those executions are
   priced rather than left pending.

## 8. Clean up

```bash
docker rm -f csa-credit-demo-db
rm -rf /tmp/csa-credit-demo
```

Stop the gateway and the web console with Ctrl-C, and close the terminals that loaded
`demo.env`. Nothing on `csa_dev_db` was touched.

## Known gaps seen in the 2026-11-13 run

- On the Tenants page, a company with no plan is offered "5 Dec 2026" and later 5ths as quick
  dates, but its first plan must start inside the current cycle; those dates are refused with the
  "first plan must start inside the current billing cycle" sentence.
