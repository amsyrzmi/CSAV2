# Subscription renewals keep the original day of the month

Short design, lane B follow-up to B1. Date: 2026-10-28.

## Intent

The owner decided on 2026-10-28 that a renewal stays on the day of the month its billing cycle
started. Today each renewal adds one interval to the previous renewal, so a short month pulls the
date back for good:

- today: 31 Oct → 30 Nov → 30 Dec → 30 Jan;
- after this change: 31 Oct → 30 Nov → 31 Dec → 31 Jan → 28 Feb → 31 Mar.

Yearly plans behave the same way. A cycle that starts on 29 Feb renews on 28 Feb, and on 29 Feb
again in leap years.

## Goals

- Renewals follow the cycle start's day, clamped to the month's last day only when the month is shorter.
- All renewal arithmetic keeps using the tenant's calendar (`clients.timezone`), as B1 does.

## Non-goals

- No billing or charging change; nothing is charged, as before.
- No UI change. The tab already shows `renews_at`.

## Owner rules (2026-10-28)

- **Extend renewal:** when staff choose a new renewal date, later renewals follow the new date's
  day. For example, a customer on the 31st extended to 15 Dec then renews on 15 Jan, 15 Feb, and so on.
- **Lapsed resume:** when a resume starts a fresh period from today, the cycle restarts from that
  resume date. This is the proposal the owner accepted.

## Approaches

- **A. Store the cycle start and count intervals from it.** The owner picked this one. Each
  subscription stores the instant its cycle is counted from. Each renewal date is that instant
  plus *n* intervals, computed in the database on the tenant's calendar. Postgres clamps the month
  end itself.
- **B. Store only the day number (and the month, for yearly plans).** Each renewal moves forward
  one interval, then snaps to that day or the month's last day. This means more hand-written date
  logic, and the 29 Feb case is awkward.
- **Rejected: derive the date from `started_at` each time.** It would ignore the extend rule.

## Design (approach A)

- **Migration `0481`** (lane B range) adds `subscriptions.renewal_anchor timestamptz`, then backfills it.
  - `EXPECTED_SCHEMA_VERSION` becomes `0481`.
  - The anchor for a row is the instant its current `renews_at` was counted from:
    - the `renews_at` set by its latest `extend_renewal` event, if it has one;
    - otherwise its latest lapsed-resume instant;
    - otherwise `started_at`.
  - Rows with no `renews_at` still get the anchor, so a later resume can use it.
- **Next renewal after a previous date `prev`:** `anchor + n × interval`, where *n* is the smallest
  whole number that puts the result after `prev`.
  - Monthly: *n* = (months from the anchor's local month to `prev`'s local month) + 1.
  - Yearly: *n* = (`prev`'s local year − the anchor's local year) + 1.
  - Both are computed as `((anchor AT TIME ZONE tz) + n * interval) AT TIME ZONE tz`.
  - The job still moves a row forward one interval per run, so a row far behind catches up over several runs, as today.
- **Where the anchor is set:**
  - **Create:** the anchor is `started_at` when `renews_at` is defaulted, and the caller's
    `renews_at` when one is given. The default `renews_at` is anchor + 1 interval, unchanged.
  - **Extend renewal:** the anchor is the chosen `renews_at`.
  - **Lapsed resume or resume with no renewal date** (executor and due job): the anchor is now,
    and `renews_at` is anchor + 1 interval, unchanged.
  - **Plan change that changes the billing interval** (immediate in the executor, or scheduled and
    applied by the job): the anchor becomes the current `renews_at`, so the new interval counts
    from the end of the period already running. A change that keeps the interval leaves the anchor alone.
- **The due job's renewal rule** uses the formula above instead of `renews_at + interval`.
- **Event payloads:** `renewed` keeps `{previous_renews_at, renews_at}`. `extend_renewal` and
  resume events need no new field.

## Constraints

- Migration in lane B's range. SQL lives in `db/sql/`. Staged RLS is unchanged (never FORCE).
  `EXPECTED_SCHEMA_VERSION` stays an exact match.
- Synthetic fixtures and disposable test databases only.
- **Staging:** apply `0481` with the `migrate` one-shot, then recreate gateway and worker. There is
  no frontend change, but recreate the frontend too if the image changes.

## Left to the builder

- Whether the column is `NOT NULL` after the backfill.
- How *n* is expressed in SQL, for example a shared SQL function or a repeated expression.
- Exact test names.
- **Tests** cover:
  - the 31 Oct and 29 Feb sequences;
  - extend, then renew;
  - a lapsed resume, then renew;
  - a month ↔ year plan change;
  - the backfill cases;
  - a non-UTC tenant across a month end.
- CONSOLE_API "Subscriptions" and a PROGRESS lane B line.
