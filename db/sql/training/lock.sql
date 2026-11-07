-- One lock per company serialises uploads, their quota check, downloads opening a file and the sweep.
SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));
