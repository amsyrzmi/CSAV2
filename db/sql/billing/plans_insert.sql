-- A new plan row (platform admin, csa.billing.plans.insert_plan). The rules are checked by the
-- caller before this runs; rows are never edited or removed. created_at is the moment of this
-- statement (clock_timestamp(), not the transaction's now()), so newest-wins orders same-date rows
-- by when they were really inserted, also when one transaction inserts two.
INSERT INTO client_billing_plans
    (client_id, stage, bundle, effective_from, reason, created_by, created_at)
VALUES (%(client_id)s, %(stage)s, %(bundle)s, %(effective_from)s, %(reason)s, %(created_by)s,
        clock_timestamp())
RETURNING id, stage, bundle, effective_from, created_at;
