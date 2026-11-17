-- The time of the list read: the transaction's start (now()), in UTC. The client echoes it as the
-- bulk route's seen_at.
SELECT now() AS as_of;
