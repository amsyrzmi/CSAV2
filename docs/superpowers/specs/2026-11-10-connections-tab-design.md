# Connections tab: TikTok Shop and Google in one place

Status: design for the owner's approval, 2026-11-10. Pull request 3 of the business surfaces programme
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
  and `?reason=`, read in `lib/tiktok-connect.ts`), `POST .../stores/{id}/disconnect`.
- **Google Calendar** (per member, needs `bookings`): the Bookings calendar tab
  (`bookings/GoogleCalendarPanel.tsx`) and an Admin status view on People & rooms
  (`GoogleCalendarPeople`). API wrappers `lib/bookings-api.ts`; routes under
  `/console/v1/bookings/calendar/` (status, connect, callback, disconnect). Returns with `?calendar=`
  or `?calendar_error=`. States `not_connected`, `connected`, `needs_reconnect`.
- **Gmail** (per member): the Email page (`email/StatusBar.tsx`); `lib/email-api.ts`; routes
  `/console/v1/email/` (context, mailbox/connect, oauth/callback, mailbox/disconnect). States
  `pending`, `connected`, `revoked`. Returns with `?notice=`.
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

- Route `app/(admin)/services/connections/page.tsx` and its portal twin, a navigation child in
  `lib/navigation.ts` in the style of `businessPages`, visible to every member (the **You** group) and
  with the **Your business** group enabled for Admins only. Name and position are confirmed in the plan;
  the owner's words were "a tab".
- Shared `ConnectionCard` (status dot, sentence, action, last activity) in `components/console/`, built
  from the existing UI building blocks; `StoreConnections`, `GoogleCalendarPanel` and the Gmail
  `StatusBar` connect controls become thin users of it, so their states and error sentences are not
  rewritten.
- Callbacks: the backend redirect targets for the TikTok, Calendar and Gmail callbacks change to the
  tab's path, keeping the query parameter names so `lib/tiktok-connect.ts` and the existing readers
  keep working. The old page paths still work for a bookmarked return.
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
