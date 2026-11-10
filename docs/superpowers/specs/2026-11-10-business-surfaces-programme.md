# Business surfaces programme: knowledge cleanup, a customer bot that knows the business, one Connections tab, product management

Status: APPROVED by the owner (Amsyar) on 2026-11-10, together with the four designs. Each piece has its own design in this folder; this
document fixes the order, the pull request grouping, the migration ranges and the owner's own tasks.

## 1. Where this came from

The owner's report on 2026-11-10, after customer bot PR A (#92) reached staging:

1. The customer bot cannot answer subscription questions, or questions about the business's own
   products and shop (for example TikTok Shop stock), properly.
2. TikTok Shop and Google sign-in are scattered; they should sit in one tab.
3. Product management for local products and for the marketplace (only TikTok Shop today) is missing.
4. Deleting a knowledge source leaves a "Needs attention" card that a tenant Admin cannot clear, and
   the cleanup and delete flow is confusing.

Survey of the code at 2e7ca4e (read-only, 2026-11-10) found the cause of each; the findings are
summarised at the top of each design.

## 2. Pull requests, in order

The grouping puts every change that touches the same code in one pull request, and keeps the pull
request count at five, plus two that wait on the owner.

| # | Pull request | Design | Migrations | Why in this place |
|---|---|---|---|---|
| 0 | Replay cases file in the image, and the planner's batch `mode` fix | none (built) | none | Already built on two branches, pushed together |
| 1 | Knowledge cleanup | `2026-11-10-knowledge-cleanup-design.md` | 0550–0559 | Live tenant confusion today, independent of everything else |
| 2 | Customer bot: answers and escalation ladder | `2026-11-10-customer-bot-business-answers-design.md` | 0560–0569 (0560 adds `escalations.note`) | Real customers are on production; the answers and the ladder change the same graph and the same golden cases |
| 3 | Connections tab | `2026-11-10-connections-tab-design.md` | none | Console navigation changes here before the Overview-as-assistant pull request changes it again |
| 4 | Product management | `2026-11-10-product-management-design.md` | 0570–0579 | Needs new write routes, a stock-authority rule and assistant actions |
| 5 | Assistant PR 3 (Overview becomes the assistant, interactive dock, alive avatar) | already planned in the assistant refinement programme; its design is brainstormed when it starts | to be decided | Last, so it renders the cards that 4 adds |

Waiting on the owner:

- **TikTok stock write-back** (Product management part B). Needs a verified Partner Center write scope
  and the owner's decision to reverse the read-only rule of the TikTok Shop connect design.
- **Single Google connection.** Needs one consent screen declaring the Gmail and Calendar scopes and
  Google's verification. Pull request 3 leaves the seam for it (a single Google card).

The HR programme (attendance, claims, payroll, recruitment, onboarding, leave self-service) keeps its
order and its migration range (0520–0529 and later) and runs after pull request 5. It shares no files
with pull requests 1 to 4.

## 3. Rules every pull request follows

- Process: design (this folder), builder check, owner approval, plan, subagent-driven build, one
  whole-branch review, owner merges. CI is the merge gate: the full suite runs in pull request CI,
  locally run only the changed files' tests and `make lint`.
- Migrations are additive and ordered. Any migration that rebuilds a check constraint reads the live
  constraint with `pg_get_constraintdef` and adds to it, as 0531 does. Whoever merges raises
  `EXPECTED_SCHEMA_VERSION` to the highest merged version, and the new range goes into
  `db/migrations/README.md` in the pull request's first migration commit.
- Console additions use the shared console building blocks (`components/console/ui.tsx`) and the
  plain-words voice in `docs/CONSOLE_VOICE.md`. The portal twin of each page is changed with it.
- After any prompt change run the full `assistant_smoke` (staff assistant) and, for the customer bot,
  the golden cases plus the replay. After rollout the owner runs the pull request's retest rows.
- PR CI runs only changed test files: grep every browser stub of a call that is added or removed and
  run those files by hand before pushing.
- The repository is public: synthetic data only, no real addresses, hostnames or secrets in git.

## 4. Owner tasks outside the code

These block specific parts and are listed in the matching designs.

- **TikTok Partner Center** (`deploy/STAGING_PIPELINE.md` 12.7 to 12.11): confirm whether a shop that is
  not the owner's can authorize the app; read the real scope keys, webhook event type numbers, the
  signature recipe, the refresh refusal codes and the product status word for "on sale"; fix the
  saved redirect URL (still the old hooks URL); enable a product write package only if write-back is
  wanted.
- **Google**: the OAuth client is External and in Testing (test users only, seven-day grants). A
  production release of the Gmail scopes needs Google's verification and a yearly security
  assessment; the Calendar scope is non-sensitive. The single Google connection waits for this.
- **Knowledge content for the telco example company**: the bot cannot answer "how do I sign up", "how do
  I pay" or "which plan for whom" until those facts are in the knowledge base. This is content, not
  code; pull request 2 makes the bot say so honestly and record the gap.

## 5. What is not in this programme

Payments for subscriptions, WhatsApp media and templates, TikTok chat as a channel, TikTok LIVE,
voice, and the customer classifier's move to the Jev model are separate tracks and keep their own
places in the existing plans.

## 6. State

- 2026-11-10: the TikTok Shop app is published as a Public app and is under TikTok's review; until it is approved, only the owner's own shop is known to connect (ledger row 13). The owner's own shop is connected on staging; live product sync needed the price fix on `fix/tiktok-live-product-shape`.
- Next: the plan for pull request 1 (knowledge cleanup) is written and PR 1 is built on `feat/knowledge-cleanup`, awaiting the owner's merge and rollout (migration 0550 needs the owner's go).
