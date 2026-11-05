-- Write the named sync-progress fields of one store (set_<field> true); the others keep their
-- stored values. Creates the row on first use.
INSERT INTO public.marketplace_store_state AS s (
    store_id, client_id, orders_synced_at, products_synced_at, catch_up_cursor, backfill_cursor,
    last_error_code, problem_notified_at, updated_at
) VALUES (
    %(store_id)s, %(client_id)s, %(orders_synced_at)s, %(products_synced_at)s,
    %(catch_up_cursor)s, %(backfill_cursor)s, %(last_error_code)s, %(problem_notified_at)s,
    %(now)s
)
ON CONFLICT (store_id) DO UPDATE SET
    orders_synced_at = CASE WHEN %(set_orders_synced_at)s THEN EXCLUDED.orders_synced_at
        ELSE s.orders_synced_at END,
    products_synced_at = CASE WHEN %(set_products_synced_at)s THEN EXCLUDED.products_synced_at
        ELSE s.products_synced_at END,
    catch_up_cursor = CASE WHEN %(set_catch_up_cursor)s THEN EXCLUDED.catch_up_cursor
        ELSE s.catch_up_cursor END,
    backfill_cursor = CASE WHEN %(set_backfill_cursor)s THEN EXCLUDED.backfill_cursor
        ELSE s.backfill_cursor END,
    last_error_code = CASE WHEN %(set_last_error_code)s THEN EXCLUDED.last_error_code
        ELSE s.last_error_code END,
    problem_notified_at = CASE WHEN %(set_problem_notified_at)s THEN EXCLUDED.problem_notified_at
        ELSE s.problem_notified_at END,
    updated_at = EXCLUDED.updated_at
WHERE s.client_id = EXCLUDED.client_id;
