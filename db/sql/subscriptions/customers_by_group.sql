-- One page of the tenant's customers in a fixed group, by id. Only customers with an email, so a
-- group never lists someone who cannot be written to. %(group)s is active_subscribers (any active
-- subscription) or renewing_soon (an active subscription renewing from now to %(days)s days on);
-- %(days)s is ignored for active_subscribers. total is the whole group, for "the rest" counts.
SELECT c.id, c.full_name, c.email, count(*) OVER ()::int AS total
FROM public.contacts c
WHERE c.client_id = %(client_id)s
  AND btrim(coalesce(c.email, '')) <> ''
  AND EXISTS (
    SELECT 1 FROM public.subscriptions s
    WHERE s.client_id = c.client_id AND s.contact_id = c.id AND s.status = 'active'
      AND (%(group)s = 'active_subscribers'
           OR (s.renews_at >= now() AND s.renews_at < now() + make_interval(days => %(days)s))))
ORDER BY c.id
LIMIT %(limit)s OFFSET %(offset)s;
