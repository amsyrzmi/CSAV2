# Product management: one list for local and marketplace products

Status: design for the owner's approval, 2026-11-10. Pull request 4 of the business surfaces programme
(`2026-11-10-business-surfaces-programme.md`). Migrations 0570 to 0579. Part B (TikTok write-back) is a
separate, later pull request that waits on the owner.

## 1. Intent

An Admin can only look at products today: the catalogue and the TikTok Shop listings are two separate
read-only lists, and nothing creates or edits a local product or its stock. The owner wants product
management for local products and for the marketplace, starting with TikTok Shop.

## 2. What exists today (survey of 2e7ca4e)

- Local: `products` (sku, name, price, `stock_qty`, `reserved_qty`, generated `available_qty`,
  `is_active`; no `version` column). Stock changes today come from `orders/reserve_stock.sql`,
  `release_stock.sql`, `commit_stock.sql` (sale approval, floors at zero), the draft-expiry jobs,
  migration 0493's guards and `reservation_repair.sql` (recomputes `reserved_qty`).
  `product_channel_map` is a baseline table (product, channel, external product, SKU and shop ids,
  sync status; no `store_id`) with a unique index and no Python or SQL reader or writer.
- Marketplace: `marketplace_listings` (one row per SKU: seller SKU, price, `stock_qty`, status,
  `synced_at`), `marketplace_sync_jobs`, `marketplace_store_state`; synced by `csa/marketplace/`.
- API: `GET /console/v1/business/products`, `/products/{id}`, `/listings` (read only,
  `csa/console/business.py`). No POST, PUT or DELETE for products; the only writes to `products` are
  seed and reservation-repair SQL.
- Console: `/business/products` ("Products & stock", `BusinessWorkspace.tsx`) with a switch between
  "Your catalogue" and "TikTok Shop", marked "View only". The only link between the two is
  `same_sku_product`, an equal-SKU match at read time.
- TikTok write-back: `TikTokShopAdapter.sync_inventory` exists and nothing calls it. The TikTok Shop
  connect design rules out any write to TikTok; the staff assistant's stock read is read only; no
  write scope is verified in Partner Center.
- `csa/inventory/` has pure domain rules (authority modes, stock-change approval) with no persistence.

## 3. Goals, non-goals, anti-goals

Part A (this pull request)

- One **Products** list for local and marketplace products: a row per product with name, SKU, local
  stock, and a "Sold on" column showing each marketplace listing with its own stock, status and last
  synced time. Filters for source, in stock, needs attention (a difference between local and
  marketplace stock, an unmatched listing).
- Create, edit, archive and restore a local product; adjust local stock with a reason, audited.
- Match a marketplace listing to a local product (explicit link, with SKU equality offered as the
  suggestion), and a listing with no match stays visible and can be turned into a local product with
  one action.
- Assistant actions for the same operations as confirm cards: create product, change price, adjust
  stock (the owner's rule: whatever a screen can do, the assistant can be asked to).

Part B (separate pull request, waits on the owner)

- Push a stock level from local to TikTok Shop, only through a confirmation, a queued job and an audit
  row.

Non-goals

- Order management, shipping, variants beyond what the listing already carries, product images,
  bulk CSV import (a possible later pull request), Shopee and Lazada.

Anti-goals

- Never a silent write to TikTok: Part A writes nothing outside our database.
- Never negative available stock; reserved stock is never edited by hand.
- A marketplace sync must not overwrite a local edit, and a local edit must not overwrite the
  marketplace's own figures in Part A: they are shown side by side.

## 4. Approaches

A. **Keep two lists, add forms to the local list.** Smallest change, but it does not give the single
   product view the owner asked for and hides stock differences.

B. **One list from a read query that joins local products to listings through an explicit link
   (recommended).** The link is a new table keyed on the listing; SKU equality is only a suggestion.
   Local edits are ordinary writes with audit; the marketplace side stays read only in Part A.

C. **Make the marketplace the source of truth and mirror it into `products`.** One list for free, but
   it reverses who owns stock for stores that sell outside TikTok, and a sync bug would rewrite the
   catalogue. Rejected.

Chosen: B.

## 5. Design

Data (migration 0570 onward):

- An explicit link between a local product and a marketplace listing, keyed on the listing's id
  (`marketplace_listings` is unique on client, channel and `external_sku_id`; `seller_sku` is nullable
  and not unique). One product may link to several listings (variants, several shops); one listing
  links to at most one product. A new link table, tenant isolated, `csa_app` only; the unused
  `product_channel_map` is left alone because it has no store reference.
- A stock movement ledger (who or what, when, delta, kind: manual, sale, correction; reason; resulting
  quantity) covering every change of `stock_qty`, including `commit_stock.sql` (kind `sale`), so the
  list can show history and a reconciliation test can hold (an opening row per product is written by
  the migration; the ledger sum equals `stock_qty`). Reservations change `reserved_qty` only and are
  not ledger rows.
- `products.version`, bumped by every edit and every stock change, not by reservation changes. A stock
  adjustment applies a delta (safe against concurrent reservations); an absolute "set to N" is refused
  when the on-hand figure changed since the form or card was made.
- `products.is_active` is the archive switch (archive hides from the bot and the list by default;
  existing orders keep their SKU).

API (Business router, Admin or the member right already used for product pages; audit on every write):

- `POST /products`, `PUT /products/{id}`, `POST /products/{id}/archive` and `/restore`,
  `POST /products/{id}/stock` (delta or absolute, with reason; refuses a result below reserved),
  `POST /products/{id}/links` and `DELETE .../links/{listing}`, `POST /listings/{id}/create-product`.
- `GET /products` becomes the combined list (local rows with their linked listings, plus unlinked
  listings as rows of their own), with the filters above; the old read routes stay for their current
  users until the console no longer calls them.
- Optimistic concurrency on edits through `products.version`, so two members do not overwrite each
  other.

Console

- `/business/products` becomes the combined list; the catalogue/TikTok switch goes away. A row opens a
  drawer with details, stock history, links and the actions. The "View only" label becomes "Edit" for
  members who can edit and stays for the rest.
- Plain words for stock differences: "TikTok shows 3, you have 5. Nothing is changed on TikTok."

Assistant

- One new proposal kind `product_action` with sub-actions create, update (price, name) and
  stock_adjust, following `booking_action`, `hr_person` and `subscription_action`; registered in
  `csa/workspace/contracts.py` and `executors.py`; both `console_proposals_kind_check` and
  `console_workspace_turns_action_check` are extended in the same migration the way 0531 does.
  Product actions are single actions, not steps inside a plan (a later change). Planner rules and
  Malay examples; a planner prompt change means the full `assistant_smoke` after rollout.

Part B outline (a later design amends the TikTok Shop connect design's read-only anti-goal)

- Preconditions: a verified Partner Center write scope; the owner's decision on stock authority
  (local pushes to TikTok, or TikTok remains separate); a confirmation card that shows the old and new
  figure; a queued job with retry and a ledger row of what TikTok answered; a kill switch; a rollout
  with the first push on the owner's own shop.

## 6. Tests

- API: create, edit, archive, restore, stock adjust (below reserved refused, reason required), link
  and unlink, unique link, tenant isolation, audit rows, concurrency conflict, package gate.
- List query: local only, linked, unlinked listing, filters, "needs attention" differences.
- Console: list states, drawer, forms with validation sentences, difference wording; portal twin;
  Playwright for create-edit-adjust and for turning a listing into a product.
- Assistant: the three proposal kinds (card shape, confirm race, refusal paths), planner cases in
  `smoke_cases.json` (English and Malay), the confirmation never executes without the card.
- Grep and fix every browser stub of the product and listing reads before pushing.

## 7. Left to the builder

The drawer layout; stock history length; the list's
paging; whether `GET /products` replaces the old routes or sits beside them in this pull request.

## 8. Owner decisions to confirm

1. Local stock is the owner's own number; TikTok's figure is shown beside it and never overwritten in
   Part A.
2. The explicit link, with SKU equality offered as a suggestion.
3. The assistant gets create product, change price and adjust stock in this pull request (this adds a
   planner prompt change and the full smoke run), or they follow in a small later pull request.
4. Part B waits for the Partner Center write scope and the stock-authority decision.

## 9. Builder check (2026-11-10): calls made by the designer

- **Who may write:** Admin and staff create and edit products and adjust stock (the Business router
  already admits both and `read_only` is excluded); archive and restore are Admin only. The "Edit" or
  "View only" label follows that, as does the assistant card's permission. Change on request.
- The first survey's sentence that only seed and repair SQL write to `products` was wrong; section 2
  now lists the real writers.
