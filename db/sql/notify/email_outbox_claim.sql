-- Person mail path: claims one due row of the person email outbox (owner pool; the worker is not
-- bound to a company). Same lease shape as claim_email.sql: delivering, locked_at, attempts + 1.
-- Ids, keys and the kind only; the address is looked up (member_emails.sql) after the claim.
WITH candidate AS (
    SELECT o.id
    FROM public.console_email_outbox AS o
    WHERE o.status = 'pending'
      AND o.next_attempt_at <= now()
    ORDER BY o.next_attempt_at, o.created_at, o.id
    LIMIT 1
    FOR UPDATE OF o SKIP LOCKED
)
UPDATE public.console_email_outbox AS o
SET status = 'delivering',
    locked_at = now(),
    attempts = o.attempts + 1
FROM candidate
WHERE o.id = candidate.id
RETURNING o.id, o.client_id, o.user_id, o.kind, o.priority, o.params, o.link, o.attempts;
