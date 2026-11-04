# Overview alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring PR #67 (console redesign) into line with Go live and the knowledge base add-on, so the home screen always works and each company sees what fits it.

**Architecture:** Merge `main` after #68, keeping Go live's panel and banner inside the redesigned pages. One rule in the turn gate lets the sandbox preview answer in any mode. `OverviewHome` chooses the chat or a small home from the active packages.

**Tech Stack:** Python 3.12, FastAPI, Postgres 16 testcontainers, Next.js with node:test, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-03-overview-alignment-design.md`

## Global Constraints

- The owner handed this branch to this session on 2026-11-03. No force-push and no rebase: merge only. A preview server from the old session may be running; leave it alone.
- Wording: the role is "Admin", so "tenant admin" becomes "admin" in anything a person reads. The add-on's title becomes "Business knowledge", matching the menu. The package key stays `knowledge_base`.
- Copy, exactly: "Ask your admin to change this.", "Ask your admin to turn replies on.", "Your bot has nothing to answer from yet. Add files in Business knowledge.", "The HR owner can't be marked as leaving. Ask your admin to move HR ownership to someone else first.", "Ask the assistant at the side of the screen."
- The owner-approved Malay HR owner sentence doesn't change.
- Telegram, WhatsApp and other customer channels behave exactly as they do after #68.
- No migration, no route change, no new role. Synthetic fixtures only. No real address: the repository is public. Never touch staging.
- CI is the merge gate. Locally run each changed file's tests one at a time with `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q <file>`, plus `make lint` (never piped) and the web checks. The host is short of memory: don't run two test files at once.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A sandbox turn at a company with a `never_auto` rule or a confidence threshold.** It still holds, so the preview shows the company's rules. (Task 2)
2. **A failed package read on Overview.** The chat shows, with no error. (Task 2)
3. **A platform admin switching between a company with Customer support and one without.** The page follows the chosen company, and never shows the other's chat or home. (Task 2)
4. **A company with Customer support suspended.** It keeps the chat, and the banner stays silent. (Task 2)
5. **The Go live panel and banner after the merge.** They keep working in the redesigned pages and themes, including the night theme's contrast. (Task 1)

---

### Task 1: Merge and align wording

Waits for #68 to merge.

**Files:**
- Modify: `app/portal/page.tsx`, `components/console/InboxWorkspace.tsx`, `csa/console/services.py`, `tests/test_services_browser.py` (the four conflicts); `components/console/overview/OverviewHome.tsx`, `components/console/ServicesWorkspace.tsx`, `lib/customer-replies.ts`, `lib/hr-records.ts`, `lib/hr-person-card.ts` if it repeats the sentence; `csa/console/packages.py`, `csa/console/agents.py:25,55`, `csa/email/models.py:26`, and the docs that name them

- [ ] **Step 1: Merge `origin/main`.** Keep both sides of each import conflict. `app/portal/page.tsx` stays the redesign's `OverviewHome`; the banner moves into it (Step 2). Keep both scenarios in `tests/test_services_browser.py`, changed so its Overview banner steps run against the chat page.
- [ ] **Step 2: Put `CustomerRepliesBanner` in `OverviewHome`,** above the chat, for the chosen tenant, with `servicesHref` following the platform or portal path. Move `CustomerRepliesPanel` directly under the base group's Customer support card in `ServicesWorkspace.tsx`.
- [ ] **Step 3: Write the failing wording tests:** update `tests/customer-replies.test.mjs`, `tests/hr-records.test.mjs`, `tests/hr-person-card.test.mjs` and `tests/test_knowledge_base_addon.py::test_catalogue_entry` to the copy in Global Constraints, with the package title "Business knowledge". Run them; they fail.
- [ ] **Step 4: Change the wording.** Also correct the two sentences PR #67 lists as false, in `csa/console/agents.py` and `csa/email/models.py`, since the assistant can now send after a confirm. Run a repository search for "tenant admin" in `components`, `lib` and `csa` user-facing strings, and change what a person reads; leave code comments and role keys.
- [ ] **Step 5: Run** the changed test files one at a time, `tests/test_console_contract_snapshot.py`, `make lint` and the web checks. Regenerate `docs/console_openapi.json` if the contract asks. Expected: all pass.
- [ ] **Step 6: Commit** with `feat(console): Go live and the add-on fit the redesign's pages and wording`.

### Task 2: Preview and the small home

**Files:**
- Create: `components/console/overview/WorkspaceHome.tsx`, `lib/overview-home.ts`, `tests/overview-home.test.mjs`
- Modify: `csa/graph/policy/gate.py`, `tests/test_gate_policy.py`, `tests/test_worker_coordinator.py`, `components/console/overview/OverviewHome.tsx`, `lib/navigation.ts`, `tests/nav-visibility.test.mjs`, `docs/ASSISTANT_RETEST.md`, `docs/PROGRESS.md`, a browser file for Overview

**Interfaces:**
- Produces in `lib/overview-home.ts`: `overviewBody(packages: PackageState[] | null): "chat" | "home"`, where `null` means the read failed and gives `"chat"`; Customer support `active` or `suspended` gives `"chat"`; anything else gives `"home"`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_gate_policy.py`: `test_the_sandbox_answers_in_any_mode`, parametrised on `shadow` and `suggest`, with a `Channel.WEB` envelope: `("send", "auto")`. `test_the_sandbox_still_follows_review_rules`: `never_auto` holds, and a `by_intent` threshold holds. `test_customer_channels_keep_the_mode`: Telegram and WhatsApp still give `reject` and `approve`.
  - `tests/test_worker_coordinator.py`: a sandbox turn on a `shadow` company gets a reply, and no Telegram outbound exists.
  - `tests/overview-home.test.mjs`: the cases in the Interfaces block.
  - `tests/nav-visibility.test.mjs`: Assistant activity shows for Customer support `active` and `suspended`, not for `hr` alone, and always for a platform admin.
  - A browser scenario: an `hr`-only stub shows the small home with People & HR, "Ask the assistant at the side of the screen." and no chat; a Customer support stub shows the chat and the banner; a failed `services/active` read shows the chat; a platform admin switching companies follows the choice.
- [ ] **Step 2: Run them to confirm they fail.**
- [ ] **Step 3: Implement.** In `evaluate`, after computing the verdict helper: a `Channel.WEB` turn treats `shadow` and `suggest` as `auto`, and continues through the rest of the function. In `OverviewHome`, read the packages the same way `StatusStrip` does, without a second request if one can be shared. `lib/navigation.ts` gates `activity` on `customer_support`, with `unlocks.activity` accepting `active` and `suspended`.
- [ ] **Step 4: Write the docs.** `docs/ASSISTANT_RETEST.md` rows for the preview and the small home; a `docs/PROGRESS.md` entry.
- [ ] **Step 5: Run** the changed test files one at a time, `make lint` and the web checks. Expected: all pass.
- [ ] **Step 6: Commit** with `feat(console): the Overview chat previews in any mode, and employee-only companies get a home`.

### Task 3: CI triage

- [ ] **Step 1: Push the branch and read `gate`'s failures.** The browser tests edited after phase 4 Task 4, including `tests/test_hr_restyle_browser.py`, have never run.
- [ ] **Step 2: Fix each failure at its cause,** running that one test file locally. A failure that shows a real product bug gets its fix and a line in the PR. Repeat until `gate` passes.
- [ ] **Step 3: Update the PR description** with what this plan added and what CI found. Commit fixes with messages naming what they fixed.
