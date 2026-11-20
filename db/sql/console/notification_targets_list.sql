-- The company's notification targets for the settings page: every email target and the current
-- Telegram staff chat. Telegram targets are retired, not deleted (a new chat disables the old
-- one), so only the latest-verified Telegram row is listed, switched on or off. Other channels
-- (whatsapp, webhook) are not managed here. Never returns a secret; the API masks the address.
-- Named parameters: client_id.
SELECT t.id, t.channel, t.address, t.events, t.min_priority, t.quiet_hours, t.language,
       t.is_enabled, (t.verified_at IS NOT NULL) AS verified, t.version
FROM public.staff_notification_targets t
WHERE t.client_id = %(client_id)s
  AND (t.channel = 'email'
       OR (t.channel = 'telegram'
           AND t.id = (SELECT c.id
                         FROM public.staff_notification_targets c
                        WHERE c.client_id = t.client_id AND c.channel = 'telegram'
                        ORDER BY c.verified_at DESC NULLS LAST, c.created_at DESC, c.id
                        LIMIT 1)))
ORDER BY (t.channel = 'telegram') DESC, t.created_at, t.id;
