# Go live: design

Status: draft for the owner's approval, 2026-11-03. No migration. Small project. Builds on the knowledge base add-on (PR #66).

## Why

A company created in the console starts in `shadow`: its bot sends customers no reply, and only a hand edit changes that. It also stays `onboarding`, which background jobs skip, so knowledge base files never process and subscriptions never run. It has no brand name, so the bot calls itself "the business". Fixing each of these by hand doesn't scale, and a silent company has no idea it's silent.

## Goals

- A tenant admin sets the brand name and turns replies on from the console, with no help from FourStacks.
- A company can prepare before going live: files process as soon as Customer support is active.
- A company can't be silent without knowing it.

## Non-goals

- A guided setup checklist (option B). It can come later, on top of this, once the console settles.
- Other brand settings: tone, persona, languages.
- Whether a channel is connected. A company with replies on and no channel simply gets no messages.
- Billing, or who may activate packages.
- Translating the handoff message. It's separate, and worth doing soon.

## Anti-goals

- Replies turned on with no brand name.
- A staff or read-only member changing replies, or a change with no record of who made it.
- New companies replying before someone chooses that. The default stays `shadow`.

## Owner decisions (2026-11-03)

1. New companies stay quiet until a tenant admin turns replies on.
2. The tenant admin chooses "staff approve each reply" or "bot replies by itself".
3. **Option A:** a "Customer replies" panel on the Customer support card in Services, plus warnings.
4. Going live is blocked only by a missing brand name. Everything else is a warning, shown only where it applies.

## Approaches

- **A. A panel on the Customer support card, plus banners (chosen).** Small, uses an existing page, and needs no new menu entry, so it stays out of the console redesign's way. Nothing walks a new client through setup; the banner points them to the panel.
- **B. A guided setup checklist on Overview (not now).** The better experience for a new client, but about twice the work. It has to know each package's setup steps, and the redesign rewrites Overview.

## How it works today

- **Mode:** `set_deployment_mode` in `csa/ops/runbooks.py` updates `clients.deployment_mode` and `config.policy.deployment_mode`. The gate reads the second through `turn/get_client_policy.sql` on each turn, and channel routes read either, so a change takes effect on the next message.
- **Status:** `clients.status` defaults to `onboarding`. `jobs/list_active_clients.sql` visits only `pilot` and `active`. `scripts/staging_accounts.py` sets `pilot` by hand; no console route does.
- **Brand:** `clients.brand_name`, read by `turn/get_client_brand.sql`. No console route sets it.
- **Services page:** `components/console/ServicesWorkspace.tsx`, with `WhatsAppPanel` already shown when Customer support is active.
- **Roles:** `IdentityService.require_member_admin` allows a tenant admin of the company or a platform admin.

## The change

### Status

When `activate_package` activates `customer_support`, a company still `onboarding` moves to `pilot` in the same transaction, with one `console_audit` row `tenant.status_changed`. Any other status is left alone. Companies that already have an active Customer support and are still `onboarding` move when replies are first saved.

### API

Under `/console/v1/services`, behind `package_gate("customer_support")`, statuses `active` and `suspended`:

- `GET /customer-replies` for any member or platform admin returns `{mode, brand_name, can_manage, warnings}`. `mode` is `off`, `approve` or `auto`, mapping to `shadow`, `suggest` and `auto`. `warnings` is a list of codes:
  - `replies_off`: the mode is `off`.
  - `no_knowledge`: the `knowledge_base` add-on is active and the company has no active document with chunks.
  - `base_only`: Customer support is the only active customer-facing package, so most questions go to staff.
- `PUT /customer-replies` for a tenant admin or platform admin, with CSRF, and `{mode, brand_name}`:
  - `brand_name` is trimmed, 1 to 120 characters, with no control characters.
  - A mode other than `off` with a blank brand name is 422 `brand_name_required`. Turning replies off never needs one, and leaves the stored name alone if none is sent.
  - In one transaction: update the brand name and mode through `set_deployment_mode`, move `onboarding` to `pilot`, and write one `console_audit` row `tenant.customer_replies_set`, with before and after values.
  - Saving the same values changes nothing and writes no row. Two admins saving at once: the last one wins, and both are recorded.
  - Refused while Customer support is suspended: 409 `package_suspended`. The Inbox stays open for held replies, but going live isn't allowed.
- OpenAPI and `docs/CONSOLE_API.md` updated.

### Panel

A "Customer replies" panel, shown beside `WhatsAppPanel` when Customer support is active. English only, like the rest of that page.

- **Brand name:** a text box, "The name your bot uses with customers".
- **Replies:** three choices:
  - "Off: customers get no reply"
  - "Staff approve each reply: a draft waits up to 24 hours, then your team gets a ticket and the customer is told you'll follow up"
  - "Bot replies by itself: when it can't answer, it hands over to your team"
- **Save** is disabled with a reason while the brand name is blank and a mode other than off is chosen.
- **Who sees what:** staff and read-only members see the current state without controls, and "Ask your tenant admin to change this."
- **Warnings inside the panel:**
  - `no_knowledge`: "Your bot has nothing to answer from yet. Add files in Knowledge base."
  - `base_only`: "With Customer support alone, most customer questions go to your team."

### Banners

One small shared component, `CustomerRepliesBanner`, on the portal Overview and at the top of the Inbox. It reads `GET /customer-replies`.

- `replies_off`: "Your customers aren't getting replies yet." A tenant admin gets a link to Services; others get "Ask your tenant admin to turn replies on."
- `no_knowledge`, only while replies are on: "Your bot has nothing to answer from yet."
- If the read is refused or fails, it shows nothing. A banner must never break the page. A company without Customer support sees nothing, because the gate refuses.

### Reset tool

`scripts/staging_accounts.py` keeps working. Activation now sets `pilot`, so its status step finds nothing to do, and its tests change to expect that.

## Constraints

- No migration, no new role, no widened grant. Never FORCE RLS. If `csa_app` can't do something, stop and report it.
- `lib/navigation.ts`, `components/AppShell.tsx` and `lib/assistant-view.ts` stay untouched. The redesign rewrites the Overview page and may touch the Inbox, so each page gets one rendered line; whichever branch merges second keeps it.
- Synthetic fixtures and disposable testcontainers only. No real address: the repository is public.
- CI is the merge gate. Locally, run the changed files' tests one at a time, plus `make lint` and the web checks.
- One scripted staging rollout with images only.

## What must be true when it's done

- A new company has `shadow` and `onboarding`. Activating Customer support makes it `pilot`, and it still sends no replies. A file uploaded now gets processed.
- A tenant admin who sets a brand name and chooses "Bot replies by itself" gets replies on the next customer message, under that name, with no restart.
- "Staff approve each reply" holds a draft in the Inbox. "Off" sends nothing.
- A blank brand name is refused for both modes that reply, at the route as well as the page.
- Staff, read-only members and an admin of another company are refused at the route.
- Each real change writes one audit row naming the actor; a repeat writes none.
- The banner shows on Overview and Inbox while replies are off, and disappears after they're on, with no sign-in needed. A failed read leaves the page working.
- The three warnings appear only in their own conditions, including `no_knowledge` clearing once a file is processed.

## Added after the final review

The final review traced what the three modes do in the turn path, and this design's words didn't match. These changes make them match.

- **Off sends nothing:** a failed turn and an expired draft used to send the fallback sentence. Now, with replies off, they send nothing and staff still get an escalation.
- **Off doesn't mute:** an escalation while Off used to mute the conversation for 24 hours, so that customer stayed unanswered after going live. Now it opens the ticket and skips the mute.
- **"Bot replies by itself" says what happens:** a console-made company has no review rules, so no draft ever waits in `auto`. The wording no longer promises that it does.
- **"Staff approve" gives a day:** saving `approve` sets `approval_ttl_minutes` to 1440, the most `Policy` allows, up from 30. An expired draft opens an escalation with reason `approval_expired`, for every company, since the customer is told someone will follow up.
- **Hand-edited modes** read as Off, so a save can repair them. `replies_off` isn't reported while Customer support is suspended, because the panel can't act on it.

## Left to the builder

- The exact SQL, and whether the route lives in `csa/console/services.py` or a small new module.
- How "active document with chunks" is read, following `kb_readiness.sql`.
- Which packages count as customer-facing for `base_only`, taken from the catalogue, not a hard-coded list if it can be avoided.
