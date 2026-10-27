-- Transaction-level lock keyed on the request: the one deciding holds it until commit, so a
-- concurrent decision gets false here instead of queueing on a pooled connection.
SELECT pg_try_advisory_xact_lock(hashtextextended('subscription_request:' || %(request_id)s::text, 0)) AS locked;
