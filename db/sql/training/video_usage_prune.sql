-- Usage rows older than USAGE_KEEP_SECONDS no longer count towards the rolling cap: delete them.
DELETE FROM public.training_video_usage
WHERE client_id = %(client_id)s
  AND processed_at < now() - make_interval(secs => %(keep_seconds)s)
RETURNING item_id;
