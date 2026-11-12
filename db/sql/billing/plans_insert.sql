-- A new plan row (platform admin, csa.billing.plans.insert_plan). The rules are checked by the
-- caller before this runs; rows are never edited or removed. created_at takes its default.
INSERT INTO client_billing_plans (client_id, stage, bundle, effective_from, reason, created_by)
VALUES (%(client_id)s, %(stage)s, %(bundle)s, %(effective_from)s, %(reason)s, %(created_by)s)
RETURNING id, stage, bundle, effective_from, created_at;
