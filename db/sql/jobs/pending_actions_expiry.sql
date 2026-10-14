/* db/sql/jobs/pending_actions_expiry.sql */
WITH expired AS (
  SELECT id
  FROM pending_actions
  WHERE client_id = %s AND status = 'open' AND expires_at <= now()
  ORDER BY expires_at
  FOR UPDATE SKIP LOCKED
  LIMIT %s
), upd AS (
  UPDATE pending_actions pa
  SET status = 'expired'
  FROM expired e
  WHERE pa.id = e.id AND pa.client_id = %s
  RETURNING pa.id
)
SELECT count(*) AS count FROM upd;
