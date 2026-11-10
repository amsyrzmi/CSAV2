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
  `is_active`). `product_channel_map` exists in the baseline and no reader or writer was found.
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

B. **One list from a read query that joins local products to listings through the mapping table
   (recommended).** The link is explicit (`product_channel_map` if it fits, otherwise a new link
   table, builder decides after reading how it was meant to work), SKU equality is only a suggestion.
   Local edits are ordinary writes with audit; the marketplace side stays read only in Part A.

C. **Make the marketplace the source of truth and mirror it into `products`.** One list for free, but
   it reverses who owns stock for stores that sell outside TikTok, and a sync bug would rewrite the
   catalogue. Rejected.

Chosen: B.

## 5. Design

Data (migration 0570 onward; builder confirms what `product_channel_map` already offers before adding):

- An explicit link between a local product and a marketplace listing (store, seller SKU), unique per
  listing, tenant isolated, `csa_app` only.
- A stock movement ledger for local adjustments (who, when, delta, reason, resulting quantity), so the
  list can show history and the assistant can explain a change; `products.stock_qty` changes only
  through it.
- `products.is_active` is the archive switch (archive hides from the bot and the list by default;
  existing orders keep their SKU).

API (Business router, Admin or the member right already used for product pages; audit on every write):

- `POST /products`, `PUT /products/{id}`, `POST /products/{id}/archive` and `/restore`,
  `POST /products/{id}/stock` (delta or absolute, with reason; refuses a result below reserved),
  `POST /products/{id}/links` and `DELETE .../links/{listing}`, `POST /listings/{id}/create-product`.
- `GET /products` becomes the combined list (local rows with their linked listings, plus unlinked
  listings as rows of their own), with the filters above; the old read routes stay for their current
  users until the console no longer calls them.
- Optimistic concurrency on edits (the existing `version` convention) so two Admins do not overwrite
  each other.

Console

- `/business/products` becomes the combined list; the catalogue/TikTok switch goes away. A row opens a
  drawer with details, stock history, links and the actions. The "View only" label becomes "Edit" for
  members who can edit and stays for the rest.
- Plain words for stock differences: "TikTok shows 3, you have 5. Nothing is changed on TikTok."

Assistant

- New proposal kinds `product_create`, `product_update`, `stock_adjust` on the existing proposal and
  confirm machinery (the constraint lists are extended in the same migration the way 0531 does),
  planner rules and Malay examples. A planner prompt change means the full `assistant_smoke` after
  rollout.

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

Link table versus reuse of `product_channel_map`; the drawer layout; stock history length; the list's
paging; whether `GET /products` replaces the old routes or sits beside them in this pull request.

## 8. Owner decisions to confirm

1. Local stock is the owner's own number; TikTok's figure is shown beside it and never overwritten in
   Part A.
2. The explicit link, with SKU equality offered as a suggestion.
3. The assistant gets create product, change price and adjust stock in this pull request (this adds a
   planner prompt change and the full smoke run), or they follow in a small later pull request.
4. Part B waits for the Partner Center write scope and the stock-authority decision.
