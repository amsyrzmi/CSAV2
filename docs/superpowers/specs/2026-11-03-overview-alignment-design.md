# Overview alignment with Go live: design

Status: draft for the owner's approval, 2026-11-03. Small change on PR #67 (console redesign), after Go live (PR #68) merges. No migration.

## Why

The redesign makes Overview the sandbox chat. Go live makes new companies start with replies off. Together these break the home screen:

- With replies off, `csa/graph/policy/gate.py` rejects every turn, whatever the channel, so the chat gets no reply. A new company's home screen looks broken.
- With "Staff approve each reply", the chat holds a draft and says "This practice chat can't release it."
- An employee-only company, such as Syarikat Cekap, has no customers and no customer support package, yet its Overview is a customer-bot chat with replies off.

## Goals

- The Overview chat always answers, as a preview, whatever the company's reply mode.
- An employee-only company gets a home that fits it, and doesn't see customer-bot screens.

## Non-goals

- Changing what customers get. Replies off still sends them nothing.
- Making the chat a separate staff assistant. The assistant dock already does that.
- A guided setup checklist.

## Anti-goals

- A preview reaching a customer, or taking a business action it doesn't take today.
- The preview hiding that replies are off. The banner stays above the chat.

## Approaches

- **A. The gate treats the sandbox channel as `auto` (chosen).** One rule in `evaluate`. Every other review rule, such as `never_auto` or a confidence threshold, still applies, so the preview shows what `auto` would do. It's small and easy to test.
- **B. Skip the gate for the sandbox.** Simpler still, but the preview would stop showing a company's review rules, which is what it's for.
- **C. Keep the rule and explain it in the chat.** No code in the gate, but the home screen would still not answer.

For employee-only companies:

- **A. A small home page (chosen).** Overview shows links to what the company has, and points to the assistant dock.
- **B. HR Home as Overview.** Fits Syarikat Cekap, but breaks for a company with only Staff email, and a second page with the same content is confusing.

## The change

### The chat answers as a preview

In `evaluate` in `csa/graph/policy/gate.py`, a turn on `Channel.WEB` with deployment mode `shadow` or `suggest` carries on as if the mode were `auto`. All other channels are unchanged.

`lib/sandbox-chat.ts` keeps its held sentence, since a company's own review rules can still hold a preview.

### Who gets the chat

`OverviewHome` reads the active packages, which `StatusStrip` already reads.

- Customer support `active` or `suspended`: the chat, as now, with `CustomerRepliesBanner` above it.
- No Customer support: `WorkspaceHome`, a small page with:
  - links to the rail's own pages for what the company has: People & HR, Email assistant, Services;
  - one line: "Ask the assistant at the side of the screen."
- While the packages are loading: the existing loading state. If the read fails: the chat, as today, so a failed read never removes anything.

A platform admin who has chosen a company gets the same rule, for that company.

### Assistant activity

`lib/navigation.ts` gates `activity` on `customer_support`, accepting `active` and `suspended`, like the Inbox. An employee-only company's rail loses it. Platform admins keep it, as they do every entry. The page itself stays reachable by its address; it holds nothing the company shouldn't see.

### The redirects

`/chat` and `/portal/chat` still redirect to Overview.

## Constraints

- Synthetic fixtures and disposable testcontainers only. No real address: the repository is public.
- No migration, no route change.
- CI is the merge gate. Locally, run the changed files' tests one at a time, plus `make lint` and the web checks.

## What must be true when it's done

- A sandbox turn gets a reply with replies off and with "Staff approve each reply". A `never_auto` rule still holds it.
- A Telegram or WhatsApp turn with replies off still sends nothing, and with "Staff approve" still holds a draft.
- Team Jo's Overview is the chat, with the banner when replies are off.
- Syarikat Cekap's Overview is the small home, with People & HR and no chat, and its rail has no Assistant activity.
- A failed package read shows the chat and no error.

## Left to the builder

- The small home's layout, using the kit in `components/console/kit/`.
- Where the status strip goes on the small home.
