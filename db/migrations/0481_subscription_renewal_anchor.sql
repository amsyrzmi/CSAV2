-- Subscription renewal anchor: renewals keep the cycle's day of the month (31 Oct -> 30 Nov -> 31 Dec)
-- instead of drifting to the shortest month's day.
-- Adds subscriptions.renewal_anchor (the instant the cycle is counted from) and
-- public.subscription_next_renewal(anchor, after, interval, tz), the first anchor + n intervals
-- (n >= 0) strictly after `after`, computed on the tenant's calendar. Backfills the anchor for rows
-- whose renews_at is exactly the next cycle date after their started_at; extended or otherwise
-- off-cycle rows and rows without renews_at keep a NULL anchor (the row's renews_at is then used).
-- Pre-check: 0480 applied (subscriptions, plans, clients). Safe to run twice: ADD COLUMN IF NOT
--   EXISTS, CREATE OR REPLACE FUNCTION, and a backfill that only touches NULL anchors.
-- Rollback: DROP FUNCTION public.subscription_next_renewal(timestamptz, timestamptz, text, text);
--   ALTER TABLE public.subscriptions DROP COLUMN renewal_anchor.

ALTER TABLE public.subscriptions ADD COLUMN IF NOT EXISTS renewal_anchor timestamptz;

-- p_interval = 'year' counts years; anything else counts months. Local arithmetic on p_tz's calendar:
-- a + c intervals lands in the same local month (year) as p_after, a + (c + 1) in the next one.
CREATE OR REPLACE FUNCTION public.subscription_next_renewal(
    p_anchor timestamptz, p_after timestamptz, p_interval text, p_tz text
) RETURNS timestamptz
LANGUAGE sql STABLE STRICT SECURITY INVOKER
AS $$
  WITH local AS (
    SELECT p_anchor AT TIME ZONE p_tz AS a,
           p_after AT TIME ZONE p_tz AS b,
           CASE WHEN p_interval = 'year' THEN interval '1 year' ELSE interval '1 month' END AS step
  ), steps AS (
    SELECT a, step,
           greatest(
             CASE WHEN p_interval = 'year'
                  THEN extract(year FROM b) - extract(year FROM a)
                  ELSE (extract(year FROM b) * 12 + extract(month FROM b))
                       - (extract(year FROM a) * 12 + extract(month FROM a))
             END, 0)::integer AS c
    FROM local
  )
  SELECT CASE WHEN ((a + c * step) AT TIME ZONE p_tz) > p_after
              THEN (a + c * step) AT TIME ZONE p_tz
              ELSE (a + (c + 1) * step) AT TIME ZONE p_tz
         END
  FROM steps
$$;

UPDATE public.subscriptions s
   SET renewal_anchor = s.started_at
  FROM public.clients c
 WHERE c.id = s.client_id
   AND s.renewal_anchor IS NULL
   AND s.renews_at IS NOT NULL
   AND s.renews_at = public.subscription_next_renewal(
         s.started_at,
         s.renews_at - interval '1 microsecond',
         coalesce((SELECT p.billing_interval FROM public.plans p
                    WHERE p.client_id = s.client_id AND p.code = s.plan LIMIT 1), 'month'),
         c.timezone);
