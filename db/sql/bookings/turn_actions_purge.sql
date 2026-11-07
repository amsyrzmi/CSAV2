-- Retention for booking_turn_actions: remove up to %(batch_size)s rows made before %(before)s
-- that are done with: a book, change or cancel (a message retried that late is long settled), or
-- a cancel question that has run out. Run as the owner by the global retention job, for every
-- company at once: the app role never deletes what a turn key did.
DELETE FROM public.booking_turn_actions
WHERE (client_id, turn_key) IN (
    SELECT client_id, turn_key
    FROM public.booking_turn_actions
    WHERE created_at < %(before)s
      AND (action <> 'ask_cancel' OR expires_at < %(now)s)
    LIMIT %(batch_size)s
)
RETURNING client_id;
