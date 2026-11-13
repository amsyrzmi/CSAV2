-- Whether an invoice is for a period its subscription will not run: the subscription cancels at
-- period end and the invoice's period starts at or after renews_at (the reminder queue and mail
-- job send nothing for it; the due job voids it when the period ends). No lock.
SELECT EXISTS (
    SELECT 1
    FROM public.invoices i
    JOIN public.subscriptions s ON s.client_id = i.client_id AND s.id = i.subscription_id
    WHERE i.client_id = %(client_id)s AND i.id = %(invoice_id)s
      AND s.cancel_at_period_end AND s.renews_at IS NOT NULL
      AND i.period_start >= s.renews_at
) AS dropped;
