-- The company this transaction is scoped to (set by tx()); NULL when none is set.
SELECT nullif(current_setting('app.client_id', true), '')::uuid AS client_id;
