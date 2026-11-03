# Knowledge base as its own add-on: design

Status: draft for the owner's approval, 2026-11-03. Migration **0530**. Small change. Go live is a separate project that follows this one.

## Why

The owner wants the knowledge base agent to be a feature a client can choose to buy. Today it sits inside the Customer support package, so every company that buys Customer support gets it.

## Goals

- A new add-on, `knowledge_base`, sold like Subscriptions and Bookings. It requires `customer_support`.
- Customer support on its own keeps the Inbox, channels, tickets, handoff to staff and order approvals.
- No company loses anything when this ships.

## Non-goals

- Go live, its warnings and the brand name screen. They come next and depend on this.
- Billing. As with every package, a platform admin activates it.
- Any change to how the knowledge base itself works.

## Anti-goals

- A company's bot going quiet, or its files becoming unreachable, because of the rollout.
- A company with only Customer support reaching knowledge base screens, routes or the assistant's offer to open them.

## Owner decisions (2026-11-03)

1. The knowledge base becomes its own add-on, built before Go live.
2. The one-line change in `lib/navigation.ts` is allowed, as an exception to the rule that the console redesign session owns that file. Whichever branch merges second keeps the line.

## Approaches

- **A. A new add-on in the catalogue (chosen).** It uses the existing package model, so Services, activation, suspension and gates all work without new logic. There is one migration for existing companies.
- **B. Sell it without requiring Customer support (ruled out).** The bot would have no Inbox or channels to answer through.
- **C. Keep it inside Customer support with an on/off flag (ruled out).** It wouldn't appear in Services as something to sell, and it would be the only feature working that way.

## How it works today

- **Catalogue:** `csa/console/packages.py`. `customer_support` lists `kb_agent` among its capabilities and `knowledge_base` among its tabs.
- **Gates tied to `customer_support` that belong to the knowledge base:**
  - `csa/console/kb_uploads.py:177`, every file route
  - the `/kb/*` routes in `csa/console/router.py`, through `support = package_gate("customer_support")`
  - `csa/workspace/rules.py:154`, the assistant's navigation rule
  - `lib/navigation.ts:13`, the menu
- **Gates that stay on `customer_support`:** the Inbox (`csa/console/inbox.py`), channels (`csa/console/channels.py`) and the inbox decision card (`csa/workspace/executors.py`).
- **Activation:** `activate_package` in `csa/console/services.py` checks requirements and switches on the package's capabilities. `suspend_package` refuses while a dependant is active, and `_held_elsewhere` keeps a capability on while another active package holds it.
- **Records:** `console_service_packages` is one row per company and package, with status `requested`, `active` or `suspended`. `0450` backfilled it, and a test compares `0450`'s member list with the code catalogue.

## The change

- **Catalogue:**
  - Add `knowledge_base`: title "Knowledge base", kind `addon`, requires `customer_support`, capabilities `kb_agent`, tab `knowledge_base`, sellable, with the summary "Answers customer questions from the files you upload."
  - Take `kb_agent` and the tab off `customer_support`, whose summary becomes "A shared inbox for your channels, with tickets, handoff to your team and order approvals. Without an add-on, most customer questions go to your team."
- **Gates:** the four above move to `knowledge_base`.
- **Migration `0530`:** for each company with a `customer_support` row, insert a `knowledge_base` row with the **same status** and the same `activated_by`, `activated_at`, `requested_by` and `requested_at`, with reference `backfill-0530`. It runs once and skips a company that already has the row. No capability row changes, so `kb_agent` stays as it is and bots keep answering. `EXPECTED_SCHEMA_VERSION` becomes `0530`. No new table, role or grant.
- **`lib/navigation.ts`:** `knowledge_base: ["knowledge_base"]`. Nothing else in that file changes. `components/AppShell.tsx` and `lib/assistant-view.ts` stay untouched.
- **The test comparing `0450` with the catalogue:** `0450` is history and stays as it is. The comparison must allow for this move, without weakening it for any other package.
- **Staging tools:**
  - `scripts/seed_staging.py` reads the catalogue and needs no change.
  - `scripts/assistant_smoke.py` lists packages for its synthetic company, and gains `knowledge_base` if any case needs it.
  - If PR #65 has merged, `deploy/staging-accounts.example.json` gives Team Jo and Kedai Mawar the add-on, and the real roster gets it too.
- **Docs:** `docs/CONSOLE_API.md` where it names the packages, `docs/ASSISTANT_RETEST.md` and `docs/PROGRESS.md`.

## Constraints

- Synthetic fixtures and disposable testcontainers only. Never FORCE RLS. No new roles.
- CI is the merge gate. Locally, run the changed files' tests one at a time, plus `make lint` and the web checks.
- One scripted staging rollout: migration 0530 with all images.
- The reset doesn't wait for this, and either order works: the migration covers companies the reset has already created, and the roster covers a reset run afterwards.

## What must be true when it's done

- After `0530`, every company that had Customer support has a `knowledge_base` row with the same status, its `kb_agent` switch is unchanged, and its files and knowledge base screens still work.
- Running the migration's rows against a company with a suspended or requested Customer support gives the same status, not `active`.
- A new company with Customer support only: `/kb/*` and the file routes refuse with the existing package refusal, the menu has no Knowledge base entry, the assistant won't open it, `kb_agent` is off, and a customer's question gets the handoff rather than an error.
- Activating `knowledge_base` without active Customer support is refused. Suspending Customer support while the add-on is active is refused, by the existing rule. Activating the add-on switches on `kb_agent`, and suspending it switches `kb_agent` off.
- The Services page shows Knowledge base as an add-on with both new summaries.

## Left to the builder

- The exact SQL of the backfill, and how the `0450` comparison test allows for the move.
- Which `/kb/*` route in `csa/console/router.py` uses `support` for something other than the knowledge base. Any such route stays on `customer_support`.
