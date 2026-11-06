-- Serialize job claims within one tenant for the rest of the transaction, so two workers can
-- never both see "no claimed job" for a store and claim two of its jobs at once.
SELECT pg_advisory_xact_lock(hashtextextended('marketplace_sync_claim:' || %(client_id)s::text, 0));
