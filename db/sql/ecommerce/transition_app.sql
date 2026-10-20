UPDATE ecommerce_apps
SET revision = revision + 1, state = %(state)s
WHERE app_id = %(app_id)s AND revision = %(revision)s AND state <> %(state)s
RETURNING app_id, revision, state;
