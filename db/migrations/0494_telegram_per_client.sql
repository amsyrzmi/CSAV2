-- 0494_telegram_per_client.sql — one active Telegram bot per company, and one company per bot.
-- Preconditions: 0491 applied (channel_credentials.disconnected_at exists). Range 0490-0499.
-- Why: the console connect (platform admin pastes a company's BotFather token) must be safe on
--   production, where nothing but an advisory lock in an operator script kept a bot on one company.
--   The staff notifier also picks the latest Telegram credential of a company, so a company with
--   two active rows would be notified through whichever was updated last.
-- Run this on the target first; both queries must return no rows or this migration stops:
--   SELECT client_id, count(*) FROM channel_credentials
--    WHERE channel = 'telegram' AND disconnected_at IS NULL GROUP BY client_id HAVING count(*) > 1;
--   SELECT shop_id, count(*) FROM channel_credentials
--    WHERE channel = 'telegram' AND shop_id IS NOT NULL AND disconnected_at IS NULL
--    GROUP BY shop_id HAVING count(*) > 1;
-- Rollback: DROP INDEX public.channel_credentials_telegram_client_key;
--   DROP INDEX public.channel_credentials_telegram_bot_key;
-- No table, column or RLS change. Nothing is disconnected silently: a duplicate stops the migration.

DO $$
DECLARE
    dup record;
BEGIN
    FOR dup IN
        SELECT client_id::text AS id, 'company' AS kind
        FROM public.channel_credentials
        WHERE channel = 'telegram' AND disconnected_at IS NULL
        GROUP BY client_id HAVING count(*) > 1
        UNION ALL
        SELECT shop_id, 'bot'
        FROM public.channel_credentials
        WHERE channel = 'telegram' AND shop_id IS NOT NULL AND disconnected_at IS NULL
        GROUP BY shop_id HAVING count(*) > 1
    LOOP
        RAISE EXCEPTION '0494: more than one active Telegram credential for % %; disconnect the extras first',
            dup.kind, dup.id;
    END LOOP;
END
$$;

-- A bot is active for one company across the deployment.
CREATE UNIQUE INDEX channel_credentials_telegram_bot_key
    ON public.channel_credentials (shop_id)
    WHERE channel = 'telegram' AND shop_id IS NOT NULL AND disconnected_at IS NULL;

-- A company has one active bot.
CREATE UNIQUE INDEX channel_credentials_telegram_client_key
    ON public.channel_credentials (client_id)
    WHERE channel = 'telegram' AND disconnected_at IS NULL;
