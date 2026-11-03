# Console redesign phase 4: sub-pages, plain pages, HR restyle and labels

Date: 2026-11-03. Same branch as phases 2, 2b and 3 (`feat/console-redesign-phase-2`, unpushed, HEAD `b787fbf`, which contains `origin/main` up to `e7494de`: HR 1b and the HR owner move). The phase 2, 2b and 3 specs stay binding for everything not changed here. Facts behind this spec were read from the code on 2026-11-03 (recon sheets `nav-split-recon.md` and `hr-restyle-recon.md` in the session scratchpad; the corrections to them are in section 9).

## 1. Owner decisions (2026-11-03, not to be re-opened)

1. Business management and the knowledge page are too long. Each becomes **separate pages**. In the left rail the entry gets a down arrow; opening it shows its sub-pages. People & HR does the same (its tabs become rail sub-pages). Three rail groups: Business management, Business knowledge, People & HR.
2. "Knowledge base" is renamed **"Business knowledge"** everywhere it names the page or tab (nav, page title, buttons, dock tab names, the backend `TAB_LABELS` that a contract test ties to the nav, tests). Kept: the service name "Knowledge base agent", the Services agent title from `csa/console/agents.py` (backend), the lowercase honesty line "Answers come from your knowledge base and products.", and "Knowledge base needs re-indexing" on Services (it names the service). "Business management" keeps its name.
3. Business and Business knowledge become simple for non-technical people while every detail stays reachable: summary first, compact long lists, two detail levels per row, an optional page-wide "Show technical details" switch, a styled upload area with progress in words, prominent unanswered questions with an "Add an answer" path, the demo behind "See an example". Nothing is deleted and every current `data-testid` stays.
4. Labelling: jargon labels are renamed on the screens redesigned in phases 2 and 3, from an owner-approved starter list plus the high-confidence rows of the label inventory, using the inventory's ten vocabulary decisions as the working vocabulary (section 8). The `Source: …` line for TikTok Shop stays exactly. Not touched: HR screens, legal, financial or privacy wording, the Malay catalogue (listed for the owner), and rows that need a backend string (except the nav/`TAB_LABELS` pair).
5. HR rail sub-pages come from the same source as `HrTabBar` (one additive function in `lib/hr-paths.ts`), obey `visibleTabs`, and the rail and mobile menu import nothing from HR. `HrTabBar` stays on the page.
6. The Phase D restyle is in scope after the pages: a token dialog instead of the legacy modal in HR, a wrapping tab list, Access page tokens and stacking, the dock and `ProposalCard` restyle (class-only), a Confirm step for "Mark as leaving" (and the WhatsApp disconnect), card-like lists below 768 px with real tables kept at desktop, a new HR width-by-theme browser test, and the clean files added to the theme-literals OWNED list. Owner-approved HR ideas 2 to 5 come **last**, each independently droppable, as additive wrappers.

## 2. Scope by stage

- **Stage 1, visible quickly.** Rail groups and the in-page section bar; Business split and simplified; Business knowledge split, simplified and renamed; the HR rail group; a preview checkpoint.
- **Stage 2, restyle.** Decision 6 items except ideas 2 to 5.
- **Stage 3, labels.** Decision 4.
- **Stage 4, HR ideas 2 to 5.** One task each; any can be dropped without affecting the others.

Non-goals: no new backend endpoint or field (the only backend text change is `TAB_LABELS["knowledge_base"]`); no change to access rules, roles or sessions; no rewording outside section 7 and the renames above; no edits to `csa/hr`, `csa/email`, `csa/workspace` logic, `csa/console/identity.py` or `app/(auth)/invite/**`; no push, PR or merge unless the owner asks.

## 3. Page maps

URLs keep their old first page so bookmarks, the dock's navigate links, the packaged-console checks and the proxy allowlist stay valid. The URL does not follow the new name (`/portal/faq` stays).

| Group (rail label, icon) | Sub-page (rail and section-bar label) | Portal URL | Admin URL | Published dock context |
|---|---|---|---|---|
| Business management (Briefcase) | Orders | `/portal/business` | `/business` | `{tab:"business", section:"orders"}` |
| | Products & stock | `/portal/business/products` | `/business/products` | `{tab:"business", section:"products"}` |
| | Store connections | `/portal/business/stores` | `/business/stores` | `{tab:"business", section:"stores"}` |
| Business knowledge (BookOpen) | What it knows | `/portal/faq` | `/knowledge-base` | `{tab:"knowledge_base", section:"library"}` |
| | Add knowledge | `/portal/faq/add` | `/knowledge-base/add` | `{tab:"knowledge_base", section:"add"}` |
| | Unanswered questions | `/portal/faq/questions` | `/knowledge-base/questions` | `{tab:"knowledge_base", section:"questions"}` |
| | Maintenance and settings | `/portal/faq/maintenance` | `/knowledge-base/maintenance` | `{tab:"knowledge_base", section:"maintenance"}` |
| People & HR (Users) | the viewer's `visibleTabs` in `HR_TABS` order (Home, People, Recruitment, Leave & attendance, Payroll, Training, Requests, Settings today) | `/portal/hr[/segment]` | `/hr[/segment]?client_id=` | unchanged (HrShell publishes it) |

Each sub-page is a literal route folder with a three-line server wrapper passing a `section` prop; each new path is a literal entry in the `proxy.ts` allowlist (no new import). Every other path under these prefixes stays a 404. The page `<h1>` stays the group name on every sub-page ("Business management", "Business knowledge"); the sub-page name is the section heading (`<h2>`). A platform administrator's chosen tenant travels in `?client_id=` across sub-pages (Business learns to read and write it; Knowledge already reads it). Rail sub-links for administrators carry no tenant (as top-level links today); the section bar does.

## 4. Rail groups

- **Data.** `NavLink` gains `children?: NavChild[]` with `NavChild = { key: string; label: string; href: string; testId?: string }`. `lib/navigation.ts` stays free of runtime imports. Top-level entries keep the one-line `{ key: "x", label: "y", href: … }` shape (a Python contract test regex-reads it). Children are built by `businessPages(admin)` and `knowledgePages(admin)` in the same file, written href-first (`{ href, key, label, testId }`) so the regex never reads a child as a tab. `navigationFor` passes `children` through; its keys stay the 13 icon keys. HR children are not in `navigation.ts`: `AppShell` adds them to the `hr` entry at run time (section 6).
- **Row.** The parent stays a real `<a>` to the first sub-page with the exact label, then a separate chevron `<button>` (`aria-expanded`, `aria-controls`, constant name "`<label>` pages"), then the child list (`<ul>`, hidden and inert when closed). Link first, button second in tab order. Children are indented text links without icons.
- **Collapsed rail (56 px, the default).** Parent icon only, no chevron, no children, no flyout. The tooltip and the exact accessible name stay.
- **Open state.** Open when the current path belongs to the group; the chevron toggles it in client state (no cookie). Navigating into a group re-opens it.
- **Current page.** A child is current on its exact path, or on a deeper path under it (People is current on `/portal/hr/people/<id>`); the child whose href equals the parent's matches exactly only; the longest match wins. The parent carries `aria-current="page"` only while its children are not shown; when shown, the child carries it and the parent gets a quieter "in this group" style.
- **Mobile menu.** The same group row inside the drawer, with the same open rule; following any link closes the drawer.
- **Top bar title** stays the group label (the existing longest-prefix rule already does that).
- **Dock.** `tabForPath` becomes prefix-aware with the same rule as the top bar, so moving between sub-pages of one group never withdraws open proposal cards; `tabHref` keeps pointing at the parent href.

## 5. In-page section bar and shared pieces

- **Section bar** (`kit/SectionNav`): a wrapping row of tab-style links under the heading, from the same `businessPages`/`knowledgePages` lists (and the admin tenant appended), `nav aria-label="Business management pages"` / `"Business knowledge pages"`, `aria-current` on the current one. It exists because the rail defaults to collapsed. After a sub-page is opened from it, focus lands on the new page's section heading.
- **Two detail levels.** Every record row: the summary line, then **Details** (the existing arrow; plain labelled groups, new text), and inside it **Technical details** (a second arrow, `<rowTestId>-tech`) holding the exact current lines, unchanged forever: ids, store codes, job numbers, attempts, raw codes such as `no_text`, sync notes.
- **Page switch** "Show technical details" (`business-tech-all`, `kb-tech-all`): a pressed-state button that opens both levels of every row on the page (including rows loaded later); pressing again closes them; a single row can still be toggled after.
- **Summary tiles** (`kit/SummaryTiles`): a row of large-number buttons with `aria-pressed`, a check mark when pressed, and "Not available" instead of a number when the read failed (never 0).

## 6. People & HR in the rail

`lib/hr-paths.ts` gains `hrNavItems(tabs, platform, tenant)` (additive), the only place an HR tab becomes a link. `HrTabBar` maps over it (same classes, same `aria-label="HR sections"`, test ids added). A new hook `components/console/hr/useHrNav.ts` reads `GET /console/v1/hr/context` once per tenant (again when the path enters HR) and returns `hrNavItems(visibleTabs(context), …)` or `null`. `AppShell` calls it inside a `Suspense` bridge (it needs `?client_id=` for administrators) and injects the items as the `hr` entry's children. While unknown, or when the read fails, People & HR is a plain link with no chevron (today's behaviour). A tab the viewer cannot open never appears.

## 7. Per-page content

### Business management

- **Orders** (`/portal/business`). Heading and intro; section bar; **four tiles** from the existing counts read: To confirm (`pending_confirmation`), To pack (`confirmed`), On the way (`shipped`), Out of stock (products). One tile is one server status, so the number on a tile equals the list it opens; "Being prepared" (`processing`) stays under All statuses. Pressing a status tile sets the status filter; pressing again clears it. The products read has no reorder level and no stock filter, so the fourth tile is **Out of stock** (`available_qty <= 0`), counted over the newest 50 products (one read, `limit=50`), captioned "Among your newest 50 products" when a further page exists; it links to Products filtered to out of stock. Then the nine status chips behind an **All statuses** arrow (`business-all-statuses`, summary "Showing: <status>"); one **Filters** button (`business-filters-toggle`) opening date, payment and the existing "More filters" source fold; the active filters as removable chips (`business-active-filters`: date, payment, status; the existing source line with "Show all sources" stays inside it). The counts note and refresh buttons stay. Order rows: Details groups **Payment** (payment status, total; the order read has no customer fields, so the owner's "Customer and payment" group is "Payment"), **Delivery** (Tracking number, Courier "not added yet" when empty, Shipped), **Record** (Marketplace order no., `Source: <channel>`, Placed, Created, Last changed here); Technical details hold today's seven lines verbatim. "About this page" and "What this page does not do yet" stay on this page (the packaged check reads them from `/portal/business`). The demo button moves to a quiet "See an example" button at the bottom ("Exit demo mode" unchanged); the demo replaces the page body as today.
- **Products & stock**. The products list (Refresh, Older), the "About the catalogue" fold, a stock chip row (All products / Out of stock, over loaded rows, with a note saying so). Details groups Stock (In stock, Set aside for orders, Available), Price, Status; Technical details: today's lines.
- **Store connections**. The disabled "Connect unavailable" / "Reconnect unavailable" buttons, the platform select and the store cards are on the page; the long technical notices sit in the existing `business-stores` fold. Details groups Marketplace, Store ID, Status, Last changed here; Technical details: today's lines.

### Business knowledge

- **What it knows** (`/portal/faq`). One sentence (`kb-status`): "Your assistant knows N things." plus " M need your attention." (M = files that could not be read + unanswered questions) or " Nothing needs your attention."; a list that failed to load is never counted as 0 ("We couldn't count everything just now."). One primary **Add knowledge** link (`kb-add-knowledge`). A prominent callout (`kb-questions-callout`) "Questions your assistant couldn't answer: N" with **Add an answer**, linking to the questions page (hidden when there are none). Then Needs attention, Being read, In your library (first 5, then **Show all N** / Show fewer, `kb-ready-show-all`), Other uploaded files, the delete confirmation and messages. Polling only while something is being read. File Details groups: Added, Size, What your assistant can use ("N parts your assistant can look up", "Nothing yet" while reading); Technical details: today's document and job lines (version, chunks, model, `job-…: failed (attempts: 3)`, raw errors).
- **Add knowledge**. A styled drop area (`kb-drop`) replacing the raw file control: drag-and-drop or a "Choose files" button; the real `<input id="kb-files" type="file">` stays in the DOM (visually hidden, focusable) with the same rules (one to three PDF, DOCX or CSV, 10 MB each). Chosen files listed; the upload button; then progress per upload in words **Waiting → Reading → Ready** (or "Could not be read") from the job list, refreshed every 5 s only while waiting or reading. The job number and the queued sentence sit in that row's Technical details.
- **Unanswered questions**. The list with "Mark resolved" as today, plus **Add an answer** per question: an inline form (question shown, "Your answer" text box) that saves through the existing `POST /console/v1/kb/documents` (text knowledge; title = the question, cut to 255 characters), then offers Mark resolved. A link "Upload a file instead" goes to Add knowledge. The button is disabled while saving (the endpoint takes no idempotency key).
- **Maintenance and settings**. The existing Maintenance and Settings sections, open on arrival.
- Locked package (`package_inactive`): every sub-page shows today's locked sentence and refresh button.

## 8. Labelling rules (stage 3)

Source: `label-inventory.md` (153 rows, 117 high; copied into `docs/superpowers/specs/2026-11-03-console-redesign-phase-4-facts/label-inventory.md` by the first label task so it travels with the branch).

1. Technical details are verbatim forever; a rename never enters them. Where the owner's list renames a line that now lives in Technical details (Platform order, Carrier, Local update), the plain Details level built in stage 1 already carries the new words and the technical line stays.
2. A row is applied only if (a) it is on Overview/chat, Assistant activity, Business, Business knowledge, Inbox, Email, Subscriptions, Team, Password, Services or the shell, (b) it is the owner's starter list or marked high confidence, (c) its text lives in the web app (or is the nav/`TAB_LABELS` pair), (d) it is not on the do-not-touch list below, (e) it is not a `Source: …` line for TikTok Shop.
3. Working vocabulary (inventory decisions 1 to 10, owner ruling 2026-11-03): **Company** for tenant (role names **Admin / Staff / View only**); **Assistant**, not agent; **Task** (Activity), **Reading** (knowledge files), **Step** (trace events); **Send / Sent / Needs your check / Hand over to staff**, one count name **Replies to check**; Subscription only for customer plans, **Included in your plan** and **Turned on / Turned off** on Services; **saved in fourstacks** for "local", **Check for new email / Last checked** for sync; **RM** on screen; **Written by the assistant** for model draft, **suggestion** for proposal; **fourstacks**, **login**, **practice chat** for product self-names. Decision 3 is superseded by the owner's "Business knowledge", but the knowledge screens still drop KB, ingest, chunk and embedding wording per the high rows.
4. Owner starter list (wins over the inventory where they differ): Orders and tracking references → Orders; Show synthetic demo → See an example; Platform order → Marketplace order no.; Carrier: Not recorded → Courier: not added yet; "Local update … not a marketplace sync timestamp" → Last changed here; Count all time → Show totals for all dates; "Tenant documents, original sources, ingestion jobs and unanswered questions" → "Files your assistant reads to answer customers, and questions it couldn't answer"; Queue upload / retry → Upload; Searchable pieces → Parts your assistant can look up; Job state → Reading status; "job-0008: failed (attempts: 3)" as a main line → "Could not be read after 3 tries" (old line under Technical details).
5. Do not touch: payroll disclaimers, the distinct-reviewer rule, "Sensitive details", the HR owner warning, recruitment consent and retention, voice consent and the recorded-voice privacy line, the email privacy line, "Cancel at period end" / "Cancel now", the knowledge delete confirmation; all HR rows (payroll, leave, recruitment, requests, training, access, settings); the assistant dock rows (not a redesigned screen; its Malay twins are excluded); every backend `csa/**` string except `TAB_LABELS`.
6. One task per two or three screens, and the role names alone, so each diff and its test edits stay local. Every test that asserts an old string changes in the same commit.
7. Listed for the owner in the PR notes, not applied: medium and low rows; the two now-false backend texts (`csa/console/agents.py` "Draft-only: nothing is sent…", `csa/email/models.py:24` "Nothing is sent to anyone…"); the Malay strings in `lib/assistant-copy.ts` that need matching changes; raw backend values shown to users (`low_confidence:0.42<0.80`, `tiktok_shop`, `pending_confirmation`, `MYR`) as a follow-up for display maps.

## 9. Corrections to the recon

- Line references in `nav-split-recon.md` for `lib/shell-state.ts` (NAV_ICONS is at :27-41, `activeNav` at :46-56) and `MobileMenu.tsx` (74 lines; the nav is at :50-66) are wrong; the facts hold.
- The Python contract test collects every `{ key: "…", label: "…"` match into a dict, so children written that way could overwrite a tab label. Children are written href-first.
- The products read has no reorder level and no stock filter or count: "Low stock" is impossible; the honest tile is "Out of stock" over the newest 50.
- The order read has no customer fields: the "Customer and payment" group is "Payment".
- The order filter takes one status: "To pack" is `confirmed` only.
- An unused text-knowledge endpoint (`POST /kb/documents`) exists; "Add an answer" reuses it (no new backend).
- Parent `aria-current` is refined: only while its children are hidden, so a page never has two current links.

## 10. Testing

Node: navigation children and their href-first shape, the current-child rule, prefix-aware `tabForPath`, `hrNavItems`, the tiles' and status line's pure helpers, upload progress words. Browser (stubbed upstream): rail groups (expanded, collapsed, mobile, keyboard, focus), each sub-page at 320, 390, 768, 1024 and 1440 in cobalt, night and bureau without horizontal overflow, reduced motion, the two detail levels and the page switch, tiles and filters sending the same queries as before, drop area and progress, Add an answer, HR rail obeying `visibleTabs`, the HR restyle test. Rewritten: the Business and Knowledge browser tests that assumed one long page (named in the plan).

## 11. Calls I made (owner can overrule)

- First sub-pages: Orders and What it knows; h1 stays the group name.
- Tiles map one status each; Out of stock over the newest 50 products.
- "Add an answer" saves a text note through the existing endpoint.
- Mobile drawer uses the same open-or-closed groups as the rail.
- Ideas 2 to 5 live in a new redesign-owned folder `components/console/hr-guide/` with one mount line each in the HR files.
