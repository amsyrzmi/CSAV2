# Pictures: one picture each for payment QR, subscription plans, business products and training videos (design)

Status: DRAFT for the owner's approval (written 2026-11-15, after the builder check; nothing is built).
Owner decisions so far (chat, 2026-11-15): the QR picture is sent with payment answers **and with payment
reminders** once uploaded; the same picture feature also covers each subscription plan and each business product;
**one picture per item**; build in two slices, **Telegram first, WhatsApp and the reminder email second** ("I'll follow
your recommendation"); store the pictures **in Postgres in one shared table** (option 1 below); product pictures start
as a **small upload on the existing product card**, with full product create/edit left to PR 4; the training library
gets a **generated thumbnail** (a frame of the video), replaceable by an upload later.
Related: `2026-11-15-payments-followups-survey.md` section D (QR image survey), `2026-11-11-payments-design.md` section 9
(no QR image in Payments 1), `2026-11-13-training-library-video-design.md` (its non-goal of thumbnails is reversed
here), `2026-11-10-product-management-design.md` (PR 4).

## 1. Intent

A customer who asks "how do I pay" gets the company's DuitNow QR as a picture, not only the DuitNow ID as text, and
a payment reminder carries it too. A customer who asks about one plan or one product sees its picture. Staff see
what they sent. The training library shows a preview of each video. Every one of these is the same capability: a
company uploads (or the system generates) one picture for a thing it owns, and the bot sends that picture, as its
own message, when that one thing is the subject of a reply.

## 2. Goals, non-goals, anti-goals

Goals
- One shared picture store and one send path used by all four kinds (`payment_qr`, `plan`, `product`,
  `training_thumbnail`), so the second kind costs a rule and a console control, not a new pipeline.
- Slice 1: staff upload and preview on all three uploadable kinds; the bot sends the picture on **Telegram** (QR with
  payment answers and chat reminders, a plan or product picture on a single-subject answer); the staff console
  sandbox chat and the Inbox show it; the training library shows generated thumbnails.
- A picture that cannot be sent never costs the customer the text reply.
- No new cloud permissions: nothing in this design adds an S3 prefix, IAM grant or secret.

Non-goals
- WhatsApp pictures and the reminder **email** picture (slice 2, section 12).
- More than one picture per item, a gallery, a crop or edit tool, picture search.
- Full product create, edit and archive (PR 4). Only "set, replace, remove the picture" on a product.
- Uploaded replacement of a generated training thumbnail (the data model leaves room; no route in slice 1).
- Thumbnails for existing training videos that are already `ready` (they show a placeholder; section 9.5).
- Sending pictures on any channel other than Telegram in slice 1, and TikTok chat.
- Moving bytes to an object store (a later change behind the same read function; section 5).

Anti-goals (a failure even if it technically works)
- A picture that blocks, delays beyond a few seconds, or replaces a text reply.
- Sending several pictures for a list answer (all plans, a product search) or the same picture twice in a reply.
- A customer-visible picture whose upload any tenant member could replace (permissions mirror the thing's own
  write rules, section 6).
- A picture of one company reachable by another (RLS on both tables, tested).

## 3. Facts the design rests on (verified by reading `main` at 7fd92cd5; "not verified" is stated)

Outbound today is text only, on every channel: `OutboundMessage` (`csa/contracts/state.py:130-137`, `kind` is
`text|template|notification`, `body` min length 1); `TurnState.outbound` is a list and `coordinator.emit` builds one
message with key `{trace_id}:0` (`csa/graph/coordinator.py:398-419`); `_insert_outbound` (`csa/turn/deps.py:1242-1306`)
writes `attachments = []`, `has_attachment = false`, writes `ui` only for confirmation/options/request-phone, and inserts
the delivery row itself (`db/sql/worker/insert_delivery.sql`). `messages.attachments jsonb` and `has_attachment`
exist and are unused for outbound (`db/schema_baseline.sql:952-965`).

Delivery (`apps/delivery/main.py`): web rows are marked delivered without moving bytes (`_deliver_web`); Telegram goes
through `TelegramAdapter.send_message` (`csa/channels/telegram.py:302-360`, JSON `sendMessage`); every Telegram error
retries up to `MAX_DELIVERY_ATTEMPTS = 5` with backoff then dead-letters (`_record_failure`); terminal failures exist
only for WhatsApp codes and an explicit `DeliveryFailure(terminal=True)`. `db/sql/delivery/claim.sql` holds a
conversation's later rows behind earlier pending ones and orders by `(created_at, id)`; `messages.created_at` and
`outbound_deliveries.created_at` default to `now()` (transaction start) and `id` is random, so **two rows inserted in
one transaction have no guaranteed order**. The same ordering feeds the Inbox list (`db/sql/inbox/list_messages.sql`).
The delivery process already holds a tenant-scoped `csa_app` pool and has **no object store**; its HTTP client has a
10 s write timeout. There is no `sendPhoto`, multipart or WhatsApp media code, and no bytea use anywhere in the repo.

`Ui` (`csa/contracts/facts.py:56-60`) is `extra="forbid"` and delivery re-validates stored `messages.ui` with it
(`apps/delivery/main.py:522-549`): a new key on `Ui` would make an older delivery process reject newer rows.
`AgentResultData` (`csa/contracts/facts.py:98-101`) carries `ui` today; `collect` merges it
(`csa/graph/coordinator.py:267-285`). Facts carry no plan code or SKU (ids are constants such as `subscription_plan_N`,
`product_{sku}`). The approval path re-inserts the text with empty attachments and copies `ui` only for
confirmation/options (`csa/approval/service.py:316-353`). The LLM history query reads `redacted_body` for both
directions (`db/sql/turn/recent_messages.sql`).

"Web chat" is the **staff console sandbox** only (`db/sql/console/chat_turns.sql`, `csa/console/chat.py:57-59`,
`ChatMessages.tsx:53-66`), which renders `{id, body}` as text. There is no public customer web chat
(`docs/CSA_SERVICE_SCOPE_BRIEF_FOR_CLAUDE.md:95`, an untracked file: treat as unreviewed). The Inbox renders
`message.body` in a `<p>` (`web/components/console/inbox/MessageItem.tsx`), so an unknown message kind shows its
caption and does not break.

Products: `products.image_url` exists (`schema_baseline.sql:1190`, exposed by `v_product_catalog` :1550-1567) and
**nothing writes it**; the console product routes are GET only (`csa/console/business.py:512,569`); the TikTok sync
writes `marketplace_listings.image_url` (`db/sql/marketplace/upsert_listing.sql:1-22`, header: "Never touches
products"), so a picture uploaded for a product cannot be overwritten by a sync. Plans: `public.plans`
(`schema_baseline.sql:1156-1167`, key `(client_id, code)`), console CRUD in `csa/subscriptions/api.py:99-117` and
`web/components/console/SubscriptionsWorkspace.tsx:910-1085`. Payment methods: `payment_methods`
(`0590_payments_core.sql:192-205`), console in `csa/payments/api.py` and `PaymentsWorkspace.tsx:881-1060`.

Write roles today: plans, writes = platform_admin, tenant_admin, tenant_staff (`subscriptions/api.py:54-58`); payment
methods, writes = platform_admin, tenant_admin only (`payments/api.py:46-47,99-101,122-149`); products have no
writers; reads are any human tenant member. Whether `package_gate("orders_products","marketplaces")` means any-of or
all-of is **not verified**.

Upload template: `csa/console/multipart.py` (`upload_form`, `BoundedParser`, 10 MiB cap) and `csa/hr/documents.py`
(`sniff` requires the extension to agree with the magic bytes, `_checked_name`, SHA-256 and size). Frontend: `web/lib/api.ts`
(CSRF header, `FormData` without a content type); helpers `lib/training-api.ts:108-112`, `lib/hr-records-api.ts:46-56`.
`next.config.ts` sets `proxyClientMaxBodySize` 31 MiB; gateway/proxy body limits outside the repo are **not verified**.

Migrations: highest is `0601_training_video_tables.sql`, `EXPECTED_SCHEMA_VERSION = "0601"`
(`apps/gateway/main.py:74`); 0592-0599 are reserved for Payments 2, 0600-0609 for the training video. Recent tables
use ENABLE (not FORCE) RLS with `tenant_isolation` on `client_id`, then `REVOKE ALL ... FROM PUBLIC, csa_ro,
csa_identity` and `GRANT SELECT, INSERT, UPDATE, DELETE TO csa_app` (`0601:123-137`).

Training video: the worker pipeline is `csa/training/video_transcribe.py` `_run` (:257-290: probe, early `ready` for no
audio or silence, caps, chunks, windows, summary); every ffmpeg call is a hardened child (`csa/training/media.py`
`_run`/`_command`: empty environment, `cwd=/`, rlimits including address space 448 MiB and file size 8 MiB, forced
demuxer and protocol whitelist) and the capacity measurements cover audio-only (`-vn`) runs only
(`docs/TRAINING_VIDEO_CAPACITY.md:174-185`); the worker container has 256 MiB (`deploy/compose.production.yml:130`).
A `MediaRefused` escaping the transcribe stage becomes `Permanent` and deletes the uploaded file
(`video_transcribe.py:254-255`, `video_jobs.py:350-354`). The worker writes through `job.tx()`
(`csa/training/video_jobs.py:168-185`) with the `csa_app` pool, so a table granted to `csa_app` is reachable. Audio-only
uploads never reach the list (`check_playable` refuses them). The list (`ItemList.tsx:104-150`, `db/sql/training/list.sql`,
`ItemOut` `csa/training/models.py:75-97`) has no image; readers see only `ready` items, managers see every state.

Provider limits are **not in the repo and not verified**. From general knowledge only: Telegram `sendPhoto` accepts a
photo up to 10 MB with width + height at most 10,000 and a caption up to 1,024 characters, and recompresses photos.
Each must be checked against the current Telegram Bot API documentation before the send task is built.

## 4. Approaches (storage and sending)

Storage.
1. **One shared table, bytes in Postgres (chosen).** Every picture is a row; each owner points at it by
   `(kind, subject_key)`. Delivery and the worker already have a tenant-scoped database role, so no object store, IAM
   or secret is added, which matters while the AWS account is being reorganised (org move). Cost: database size
   (about 2 MB at most per picture, one per item) and backups; a later move to S3 changes only the read function.
2. The same table with bytes in the object store (the HR/knowledge-base convention): follows the repo pattern but
   needs a new S3 prefix, IAM grants and the store in gateway, worker and delivery on staging and production.
3. A separate column and upload per feature (QR first): smallest first PR but three upload and send paths.

Sending. The picture is its **own outbound message sent before the text** (a long payment reply cannot be a caption;
the longest is about 3,950 characters), with a short caption built by code. The agent asks for it through a **new
optional field on `AgentResultData`** (not a new key on `Ui`, which older delivery processes would reject).

## 5. Data model (migration `0610`, new range 0610-0619; `EXPECTED_SCHEMA_VERSION` becomes `"0610"`)

`media_assets` (metadata, small rows, listed freely):
`id uuid pk default uuid_generate_v4()`, `client_id uuid not null references clients`, `kind text not null check in
('payment_qr','plan','product','training_thumbnail')`, `subject_key text not null` (the payment method id, the plan code,
the product id, the training item id, as text), `media_type text not null check in ('image/jpeg','image/png')`,
`byte_size integer not null check (byte_size between 1 and 2097152)`, `width integer not null`, `height integer not null`,
`sha256 text not null`, `source text not null check in ('upload','generated')`, `created_by uuid null`,
`created_at`, `updated_at` timestamptz default now(); **unique `(client_id, kind, subject_key)`** (one picture per
subject; replacing is an upsert that keeps the row id stable and changes `sha256`, which is the ETag/version).
`media_asset_data` (`asset_id uuid pk references media_assets on delete cascade`, `client_id uuid not null`, `data bytea
not null`): bytes live in their own table so list queries and the metadata row never carry or detoast them.
Both tables: RLS `tenant_isolation`, `REVOKE ALL ... FROM PUBLIC, csa_ro, csa_identity`, `GRANT SELECT, INSERT, UPDATE,
DELETE TO csa_app`. `csa_ro` gets `SELECT` on `media_assets` only (reporting needs no bytes).
Per-tenant limit: at most 300 pictures (a counted check in the upload transaction, 409 `picture_limit`).
No foreign key to the subject (the table is polymorphic), so **every owner's delete path deletes its picture in the same
transaction**: payment method delete, plan (archived plans keep theirs; there is no delete today), product (no delete
today), training item delete (`db/sql/training/delete.sql`). One test per path proves no orphan is left; a sweep for
orphans is not built (YAGNI), and a missing subject at send time skips the picture (section 8).

## 6. Console: upload, read, permissions

Module `csa/media/` (service, SQL under `db/sql/media/`, contracts): `put(conn, client_id, kind, subject_key, file,
actor)`, `get(conn, client_id, asset_id)`, `delete(...)`, `delete_for(conn, client_id, kind, subject_key)`.
Upload rules (one place): JPEG or PNG only, the extension and magic bytes must agree (the HR `sniff`), at most
**2 MiB**, dimensions read from the JPEG/PNG header (not a full decode) and refused above **4096 x 4096** or below
**64 x 64**, one file per request through `upload_form` (`max_files=1`, field `file`), refused for a read_only role,
CSRF as for every console write. A QR should be uploaded at 800 px or more (a hint in the form; Telegram recompresses).
Routes (writes mirror the owner's own write rules; each is audited `media.picture.set` / `media.picture.removed`):
- `PUT|DELETE /console/v1/payments/methods/{id}/picture`: admin only, `qr` methods only (422 `picture_kind_not_qr`).
- `PUT|DELETE /console/v1/subscriptions/plans/{code}/picture`: the plan writers (platform_admin, tenant_admin,
  tenant_staff).
- `PUT|DELETE /console/v1/business/products/{id}/picture`: platform_admin, tenant_admin, tenant_staff (a new rule,
  the same people who can see products today; PR 4 may tighten it).
- **One read route** `GET /console/v1/media/{asset_id}`: any human tenant session (read_only included; a platform admin
  passes `client_id`), returns the bytes with `Content-Type`, `ETag` = `sha256`, `Cache-Control: private, no-cache`
  (revalidate), `nosniff`. It refuses `training_thumbnail` assets (404): those are served only by the training route
  (section 9) so the "readers see only ready items" rule holds.
Existing out-contracts gain `picture: {id, version} | null` (`PlanOut`, the payment method out, `BusinessProduct`) and the
training `ItemOut` gains `thumbnail: {version} | null`; the contract snapshot and `docs/console_openapi.json` are
regenerated. Web: one `PictureField` component (preview through the read route, "Choose picture", "Replace", "Remove",
the limits in plain words, errors for too big / wrong type / unreadable) used on the QR method form, the plan edit
row and the product card.

## 7. Where the bot sends a picture (rules; at most one picture message per reply)

The agent sets `AgentResultData.picture = PictureRef(kind, subject_key)` only when **one** thing is the subject:
- **Payment QR:** the payment agent's "how to pay" answer (the instructions answer, and the status answer when it lists how
  to pay): the first active `qr` method, by sort order, that has a picture. Chat reminders (section 10).
- **Plan:** sign-up `readback` (the plan being signed up for), and a `status`/`mine` answer that names exactly one
  subscription. Never `plans` (list), the "which plan" question, pause/cancel/change answers.
- **Product:** the single-product detail answer (`_detail`, by SKU). Never search, availability or the order draft.
- **Training thumbnail:** never sent to customers.
Priority when several agents in one turn ask: the order of the coordinator's results; the first picture wins and the rest
are dropped (so "pay" plus a plan question sends the QR). `coordinator.emit` turns the chosen `PictureRef` into one
extra `OutboundMessage` of **`kind="image"`** with the asset id in `attachments` and a caption from code
(`DuitNow QR: {label}`, the plan or product name; Malay `QR DuitNow: {label}`; no amounts, so no grounding change).
It is created only for a channel that supports it (**Telegram in slice 1**; WhatsApp and web-API rows get no image
message, the text reply is unchanged). A held-for-approval reply that is later approved sends **text only** (the
approval path does not copy attachments; stated, cost small). The LLM history query **excludes `kind='image'`** rows.

## 8. Sending, order and failure (the part that must not hurt the text reply)

- **Order:** `_insert_outbound` writes the image message and its delivery row first and the text second, both with
  `created_at = clock_timestamp()` (strictly increasing inside the transaction) instead of the `now()` default, so
  `claim.sql` and the Inbox order them image, then text. Tests assert the order for two rows written in one transaction.
- **Delivery:** `get_message.sql` also selects `kind` and `attachments`; for `kind="image"` the delivery worker reads the
  bytes from `media_asset_data` by asset id (tenant-scoped pool), and `TelegramAdapter.send_photo(chat_id, data,
  media_type, caption)` posts multipart `sendPhoto`. Because the asset is read at send time, a replaced picture sends
  the new one and a removed picture is detected.
- **Failure policy for image rows only (never for text):** at most **two attempts**; a missing asset or subject, an
  invalid or too-large picture, a Telegram rejection, or the second failure ends the row as **skipped with a coded
  reason** (`picture_unavailable`, `picture_rejected`, `picture_failed`) that appears in the delivery dead letters and
  the existing staff notice, and the text row proceeds. A rate-limit `retry_after` is honoured within the two attempts.
  (Builder detail: reuse the existing delivery status vocabulary; add a terminal "skipped" only if none exists.)
- **Rolling deploy:** the contract change is optional-default, and **PR 2 ships delivery support before any emitter** (the
  emitting change is a later PR), so no older delivery process meets an `image` row.
- **Staff views:** the sandbox chat returns an image message as `{id, body, picture}` and renders it inline through the
  read route; the Inbox renders an `image` message as a small thumbnail with its caption.
- **QR scannability risk (stated, not solved here):** Telegram recompresses photos; a small or highly compressed QR may
  stop scanning. Mitigation: the 800 px hint; acceptance test = the owner scans the QR received on a phone from
  staging. If it fails, the fallback is to send the QR with `sendDocument` (uncompressed; shown as a file).

## 9. Training library thumbnail (generated)

Reverses the non-goal at `2026-11-13-training-library-video-design.md:46` (that line gives no reason beyond "the uploaded
file is what plays").
1. **Where:** a new `extract_frame` beside `extract_chunk` in `csa/training/media.py`, through the same hardened `_run`
   (empty environment, rlimits, forced demuxer, protocol whitelist): `-ss <t> -map 0:v:0 -an -sn -dn -frames:v 1`, scaled
   to 320 px wide, JPEG to stdout (stays under `PROBE_MAX_OUTPUT_BYTES` 256 KiB; if the build's JPEG encoder is missing,
   the build task picks PNG or WebP and says so). `t = min(1 s, 10% of the duration)`.
2. **When:** in `video_transcribe._run` straight after a successful probe and **before** the no-audio/silence early
   returns (so those videos get one), skipped when `video_codec is None`. The once-only marker is the existence of the
   asset row (a retried attempt checks it first).
3. **Isolation:** the whole step catches everything (`MediaRefused`, `MediaUnavailable`, `ValueError`, storage and
   database errors) except `JobLost` and `ForeignKeyViolation`, logs a code only (no path, no stderr), calls
   `job.heartbeat()` first, and writes in its own `job.tx()`. A thumbnail failure never fails, retries or deletes the item.
4. **Measurement gate:** every ffmpeg call so far is audio-only. Before the code is written, one measurement run decodes
   a frame of large AV1 and HEVC (and a duration-less WebM seek) under the 448 MiB address-space cap and the 256 MiB
   container, in the style of `TRAINING_VIDEO_CAPACITY.md`. If a file cannot be decoded within the caps the item simply
   has no thumbnail; the limits are not raised.
5. **Storing and showing:** an asset with `kind='training_thumbnail'`, `subject_key = item id`, `source='generated'`
   (the marker a future upload must not overwrite). `db/sql/training/delete.sql` deletes it in the same transaction as the
   item. `list.sql` adds `thumbnail` (version only, never bytes). New route `GET /console/v1/training/items/{id}/thumbnail`
   under the existing router and gates, with the item's own visibility (readers: `ready` only), `ETag`, `Cache-Control:
   private, no-cache`. `ItemList.tsx` gets a fixed-size `<img>` as the first child of each row, with a placeholder for a PDF
   or an item with no thumbnail; `VideoPlayer.tsx` uses it as `poster`. Existing `ready` videos show the placeholder
   (no backfill in this design: small number, a later "make previews" action if wanted).
6. **Tests that change:** `test_training_video_transcribe.py` pins exactly 5 store sources for a 3-chunk video
   (probe, loudness, 3 chunks): the thumbnail step must not read through the store counter or the pin is updated
   deliberately; `test_training_media.py` limits and hostile-container cases get a thumbnail case; the `make_video` helper
   (`tests/training_media.py`) feeds the frame test.

## 10. Reminders

`payments.notices.chat_message` is called by the reminder job once per reminder with key `invoice:{id}:reminder:{step}`
inside one transaction. When the company has a QR picture and the chat is a Telegram chat, the job also queues the image
message first (same ordering rule, key `...:qr`), on **every reminder step**. WhatsApp chat reminders stay text-only in
slice 1 (no WhatsApp media), and the **reminder email** gets no picture in slice 1 (the mailer has no inline or attachment
support; `OutgoingMail`, `csa/console/mailer.py:41-46`). The existing rules are unchanged (WhatsApp only within the last
23 hours, quiet hours, replies-off).

## 11. Pull requests (four, each independently shippable; one migration, in PR 1)

1. **Picture store and console upload:** migration 0610, `csa/media`, the read route, the three upload routes, the
   `PictureField` on the QR method form, plan row and product card, contracts/OpenAPI, delete paths, tests. Staff can
   upload and preview; nothing is sent yet.
2. **Outbound image path and the QR:** `OutboundMessage.kind="image"`, `AgentResultData.picture`, ordering, delivery
   (`send_photo`, failure policy), Inbox and sandbox display, the payment agent's QR rule, reminders on Telegram.
   Acceptance: the owner receives the QR on Telegram from staging and scans it. Delivery support ships before emitters.
3. **Plan and product pictures in single-subject answers.**
4. **Training thumbnail** (after its measurement run).
Slice 2 (WhatsApp, reminder email) is a separate short design addendum after PR 2 is proven on Telegram.

## 12. Slice 2 (outline only, not designed here)

WhatsApp: send an image message inside the service window (upload media or a link; Meta limits and caption rules to be
checked against current Meta documentation, **not verified**), the window check and terminal codes already in delivery,
and the same failure policy. The client's own WhatsApp Business account is connected by its tenant admin in the console
(`PUT /console/v1/channels/whatsapp`); the deployment's Meta app and its review are the owner's and gate any live test.
Reminder email: an inline image (`add_related`) in `payment_reminder_mail`.

## 13. Review focus (inputs the spec implies and tests must pin)

- Two rows written in one transaction are delivered image first, then text, and shown in that order in the Inbox.
- An image that fails twice never delays or drops the text; a deleted picture between emit and send is skipped with
  `picture_unavailable`.
- A picture of company A is not readable by company B (read route, delivery read, worker write): tests on both tables.
- Replacing a picture changes the version and the next send uses the new bytes; removing it stops sending it.
- A PNG renamed `.jpg`, a 3 MB file, a 5000 px file, an SVG, a zero-byte file and a polyglot file are refused with plain
  messages; a read_only user cannot upload; the QR route refuses a non-`qr` method.
- A reply that lists plans or products sends no picture; "pay" plus a plan question sends one picture (the QR).
- A held-for-approval reply approved later sends text only (stated behaviour).
- A thumbnail failure never fails a transcription job; audio-only never reaches it; a video with no audio still gets one.
- Deleting a training item, a payment method or (later) a product deletes its picture in the same transaction.

## 14. Calls I made (owner can override; each says what it costs if wrong)

- **2 MiB, 64-4096 px, 300 pictures per company.** Cost if wrong: a limit change, no migration (the byte_size check is
  widened in a migration only if raised above 2 MiB).
- **Bytes in a separate table.** Cost if wrong: none for readers; a later move to S3 replaces `media_asset_data`.
- **Product picture write roles = plan writers (admin and staff).** Cost: PR 4 may tighten it.
- **Reminders carry the picture on every step.** Cost: noisier reminders; a one-line rule change to "first step only".
- **Approved-held replies send text only.** Cost: a held reply loses its picture (rare).
- **The image message is excluded from model history.** Cost: the model does not know a QR was sent.
- **No thumbnail backfill, no replace-upload for training.** Cost: placeholders on old videos until a later action.
- **Provider numbers unverified; verified before PR 2 is built.** Cost: a number in this document may change.
