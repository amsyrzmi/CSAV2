-- plan_get.sql under a share lock, for the change_plan executor: a concurrent plan PATCH (which
-- locks the row FOR UPDATE, plan_lock.sql) waits until this transaction ends, so an archive or a
-- price edit cannot land between the read and the subscription update.
SELECT code, name, price, currency, billing_interval, features, sort_order, is_active
FROM public.plans
WHERE client_id = %(client_id)s AND code = %(code)s
FOR SHARE;
