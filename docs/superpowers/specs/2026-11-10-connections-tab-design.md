# Connections tab: TikTok Shop and Google in one place

Status: APPROVED by the owner on 2026-11-10 (every section 8 decision answered yes, defaults as written). Pull request 3 of the business surfaces programme
(`2026-11-10-business-surfaces-programme.md`). No migration.

## 1. Intent

An Admin has to hunt for the places that connect the business to the outside: TikTok Shop is under
Business, Google Calendar under Bookings, Gmail under Email. The owner wants them in one tab with one
look and one set of words, and wants Google presented as one thing.

## 2. What the console does today (survey of 2e7ca4e)

The console is the Next.js app in `CSA_WebConsole/csa-console/web`. Each page exists twice, under
`app/(admin)/...` and `app/portal/...`; the rail and sub-navigation live in `lib/navigation.ts`.

- **TikTok Shop** (per company, Admin, needs the `marketplaces` package): `/business/stores`,
  `components/console/business/StoreConnections.tsx`. Status `GET /console/v1/business/stores`;
  `POST .../stores/tiktok/connect`, callback `GET .../stores/tiktok/callback` (303 back with `?tiktok=`
  and `?reason=`, read in `lib/tiktok-connect.ts`; a platform admin returns to `/business/stores` with
  `client_id`, a tenant Admin to `/portal/business/stores`, per `csa/console/tiktok_connect.py`), `POST .../stores/{id}/disconnect`.
- **Google Calendar** (per member, needs `bookings`): the Bookings calendar tab
  (`bookings/GoogleCalendarPanel.tsx`) and an Admin status view on People & rooms
  (`GoogleCalendarPeople`). API wrappers `lib/bookings-api.ts`; routes under
  `/console/v1/bookings/calendar/` (status, connect, callback, disconnect). Returns with `?calendar=`
  or `?calendar_error=`, always to `/portal/bookings` (`BOOKINGS_PAGE` in `csa/bookings/google/api.py`). States `not_connected`, `connected`, `needs_reconnect`.
- **Gmail** (per member): the Email page (`email/StatusBar.tsx`); `lib/email-api.ts`; routes
  `/console/v1/email/` (context, mailbox/connect, oauth/callback, mailbox/disconnect). States
  `pending`, `connected`, `revoked`. Returns to `/email` or `/portal/email` (chosen by whether the
  request carried a `client_id`) with `connected=1` or `error=<code>`.
- Telegram and WhatsApp are set up by a platform admin on Services (`ServicesWorkspace.tsx`).
- Settings has only audit, clients and team.

## 3. Goals, non-goals, anti-goals

Goals

- One **Connections** tab with a card per connection: name, a status sentence in plain words, the one
  action that applies (Connect, Reconnect, Disconnect), and when something last happened.
- Two groups with clear headings: **Your business** (TikTok Shop, Admin only) and **You** (Google:
  Calendar and Gmail, each member's own).
- Every callback returns to the tab, with the existing outcome sentences.
- The old places keep a one-line status and a link "Manage in Connections", not a second copy of the
  connect controls.
- Package-locked cards show why ("Included in the Marketplaces add-on") instead of disappearing.

Non-goals

- A single Google consent and a merged Calendar and Gmail grant (waits on Google verification; this
  design leaves the seam, see section 5).
- Moving Telegram and WhatsApp (platform-admin setup, a different audience).
- Any change to what the connections do, to scopes, or to the token handling.

Anti-goals

- No second implementation of connect or disconnect: the cards call the existing API wrappers.
- A member must not see or act on another member's Google connection except through the existing Admin
  status view.

## 4. Approaches

A. **A new aggregate API `GET /console/v1/connections` and one new page.** Clean contract, but a new
   backend surface to build and secure for what the three existing calls already return.

B. **Frontend only: reuse the three status calls and wrappers, refactor the three components into
   shared cards (recommended).** No backend change except the callback redirect targets; smallest
   blast radius.

C. **Leave the pages where they are and add a status overview page that links out.** Does not
   centralise; the owner asked for the controls in one tab.

Chosen: B. An aggregate endpoint can be added later if the tab's three calls prove slow.

## 5. Design

- A new top-level navigation entry **Connections** (a new `NavKey` in `lib/navigation.ts`, route
  `app/(admin)/connections/page.tsx` and the portal twin `app/portal/connections/page.tsx`), not a
  child of Services (Services is the platform admin's Telegram and WhatsApp setup). The entry is
  visible to every member; the **Your business** group is enabled for Admins only.
- Shared `ConnectionCard` (status dot, sentence, action, last activity) in `components/console/`, built
  from the existing UI building blocks; `StoreConnections`, `GoogleCalendarPanel` and the Gmail
  `StatusBar` connect controls become thin users of it, so their states and error sentences are not
  rewritten.
- Callbacks: each of the three callbacks keeps its own mechanism and only its return page changes, to
  the tab: a platform admin returns to `/connections?client_id=...` (TikTok only), everyone else to
  `/portal/connections`. Query parameter names and values stay as they are today (`tiktok` and
  `reason`; `calendar` and `calendar_error`; `connected=1` and `error=<code>` for Gmail) so the
  existing readers keep working. The Google redirect URIs registered with Google do not change (they
  are the callbacks, not the return pages). The old page paths still work for a bookmarked return.
- Google presentation: one **Google** card with two rows (Calendar, Gmail), each with its own status
  and button for now. The later single connection replaces the two buttons with one; no other part of
  the tab changes.
- The Admin view of the team's Google calendar connections stays on People & rooms; the tab links to it.

## 6. Tests

- Component tests for `ConnectionCard` states (every status word of the three integrations).
- Browser tests: arrive from each callback with each outcome parameter and see the right card and
  sentence; locked card; Admin versus member visibility; the old pages' one-line status and link.
- Backend: the three callback tests assert the new redirect target and unchanged parameters.
- Grep and fix every browser stub of the status calls and of the three callback URLs before pushing;
  PR CI runs only changed test files.

## 7. Left to the builder

Card order, exact sentences within `docs/CONSOLE_VOICE.md`, the tab's label and place in the rail, how
the old pages show their one-line status.

## 8. Owner decisions to confirm

1. The tab holds TikTok Shop and Google only; Telegram and WhatsApp stay on Services.
2. The old pages keep a status line and a link, not duplicate controls.
3. The tab is visible to every member, with the business group Admin-only.

## 9. Builder check (2026-11-10): calls made by the designer

- **Platform admins** see only **Your business** (for the selected company) on the tab. Google
  Calendar and Gmail are personal connections and Calendar's connect already refuses a platform admin
  (`actor.client_id` must equal the company); the **You** group is hidden for them, not a bug to fix.
- **Gates on cards:** TikTok needs the `marketplaces` package and an Admin; Calendar needs `bookings`
  and a tenant Admin or staff role; Gmail needs `staff_email` and a role that is not read-only. A card
  whose package is off shows the "Included in ..." sentence; a read-only member sees the card with the
  button disabled and "Your role can't connect this".
- **Old pages keep every current state**, including the Admin view of the team's Google connections on
  People & rooms; they gain only the one-line status and the link, and lose the connect controls.
