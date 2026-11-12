-- One plan insert at a time per company, until the transaction ends ('client_billing_plans:<id>').
SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));
