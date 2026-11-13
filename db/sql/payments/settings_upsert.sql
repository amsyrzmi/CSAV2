-- First save inserts (version 1); later saves replace the values and bump the version. The caller
-- holds the payments lock and has checked the version it read.
INSERT INTO public.payment_settings (client_id, enabled, lead_days, reminder_days, suspend_after_days)
VALUES (%(client_id)s, %(enabled)s, %(lead_days)s, %(reminder_days)s, %(suspend_after_days)s)
ON CONFLICT (client_id) DO UPDATE
SET enabled = EXCLUDED.enabled,
    lead_days = EXCLUDED.lead_days,
    reminder_days = EXCLUDED.reminder_days,
    suspend_after_days = EXCLUDED.suspend_after_days,
    version = public.payment_settings.version + 1,
    updated_at = now()
RETURNING enabled, lead_days, reminder_days, suspend_after_days, version;
