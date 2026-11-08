WITH retired AS (
    UPDATE public.staff_notification_targets
    SET is_enabled = false
    WHERE client_id = %s AND channel = 'telegram' AND address <> %s AND is_enabled
    RETURNING id
)
INSERT INTO public.staff_notification_targets (client_id, channel, address, verified_at, is_enabled)
VALUES (%s, 'telegram', %s, now(), true)
ON CONFLICT (client_id, channel, address)
DO UPDATE SET is_enabled = true, verified_at = now()
RETURNING id;
