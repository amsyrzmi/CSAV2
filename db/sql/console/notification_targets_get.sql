-- One target as the settings page lists it (same visibility rule as notification_targets_list):
-- an email target, or the current Telegram staff chat. No row for anything else, including another
-- company's id. Named parameters: client_id, target_id.
SELECT t.id, t.channel, t.address, t.events, t.min_priority, t.quiet_hours, t.language,
       t.is_enabled, (t.verified_at IS NOT NULL) AS verified, t.version
FROM public.staff_notification_targets t
WHERE t.client_id = %(client_id)s
  AND t.id = %(target_id)s
  AND (t.channel = 'email'
       OR (t.channel = 'telegram'
           AND t.id = (SELECT c.id
                         FROM public.staff_notification_targets c
                        WHERE c.client_id = t.client_id AND c.channel = 'telegram'
                        ORDER BY c.verified_at DESC NULLS LAST, c.created_at DESC, c.id
                        LIMIT 1)));
