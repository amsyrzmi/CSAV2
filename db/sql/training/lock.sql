-- One lock per company serialises the writes: uploads (with their quota check) and the sweep.
-- Reads never take it.
SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));
