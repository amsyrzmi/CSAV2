-- First save inserts (version 1); later saves replace the value and bump the version. The caller
-- holds the settings lock and has checked the version it read.
INSERT INTO public.subscription_settings (client_id, pending_expiry_days)
VALUES (%(client_id)s, %(pending_expiry_days)s)
ON CONFLICT (client_id) DO UPDATE
SET pending_expiry_days = EXCLUDED.pending_expiry_days,
    version = public.subscription_settings.version + 1
RETURNING pending_expiry_days, version;
