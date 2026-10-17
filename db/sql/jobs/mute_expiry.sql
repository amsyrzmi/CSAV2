/* db/sql/jobs/mute_expiry.sql */
WITH expired AS (
  SELECT id
  FROM conversations
  WHERE client_id = %s
    AND owned_by = 'human_support'
    AND ai_muted_until IS NOT NULL
    AND ai_muted_until <= now()
  ORDER BY ai_muted_until
  FOR UPDATE SKIP LOCKED
  LIMIT %s
), upd AS (
  UPDATE conversations c
  SET status = 'open', owned_by = 'ai', ai_muted_until = NULL,
      ai_suppressed_before = clock_timestamp(), inbox_version = inbox_version + 1
  FROM expired e
  WHERE c.id = e.id AND c.client_id = %s
  RETURNING c.id
)
SELECT count(*) AS count FROM upd;
