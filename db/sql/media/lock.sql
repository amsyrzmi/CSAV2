-- Serialises a company's picture uploads until its transaction ends, so two uploads cannot both
-- pass the 300-picture check. Param: "<client_id>:media".
SELECT pg_advisory_xact_lock(hashtext(%s));
