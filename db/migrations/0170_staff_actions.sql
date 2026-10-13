-- One-time staff buttons (approve/reject) attached to a staff notification (LG05 staff path),
-- plus a lease column so abandoned notification sends can be reclaimed.

ALTER TABLE public.staff_notifications
    ADD COLUMN IF NOT EXISTS locked_at timestamp with time zone;

CREATE TABLE IF NOT EXISTS public.staff_actions (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients (id),
    notification_id uuid NOT NULL REFERENCES public.staff_notifications (id),
    target_id uuid NOT NULL REFERENCES public.staff_notification_targets (id),
    order_id text NOT NULL,
    action text NOT NULL CHECK (action IN ('approve', 'reject')),
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'used', 'expired')),
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    UNIQUE (notification_id, action)
);

ALTER TABLE public.staff_actions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.staff_actions
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON public.staff_actions TO csa_app;
GRANT SELECT ON public.staff_actions TO csa_ro;
