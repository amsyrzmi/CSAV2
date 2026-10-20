INSERT INTO ecommerce_apps (app_id, revision, state)
VALUES (%s, 1, 'disabled')
ON CONFLICT DO NOTHING
RETURNING app_id, revision, state;
