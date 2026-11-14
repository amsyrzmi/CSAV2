-- Scope the seed's transaction to one company, as csa.core.tenancy.tx does (transaction-local).
SELECT set_config('app.client_id', %s, true);
