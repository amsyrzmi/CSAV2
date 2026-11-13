-- Reserve a video's processing seconds against the company's rolling cap (cap seconds in the last
-- window_seconds). Run under the company lock, so two reservations cannot both take the last room.
-- An item reserves once: a row already there counts as reserved, and usage rows have no foreign
-- key to the item, so deleting a video and adding it again cannot reset the count. reserved false:
-- "until" is when enough reserved seconds age out of the window for this video to fit.
WITH existing AS (
    SELECT 1
    FROM public.training_video_usage
    WHERE client_id = %(client_id)s AND item_id = %(item_id)s
), recent AS (
    SELECT item_id, seconds, processed_at
    FROM public.training_video_usage
    WHERE client_id = %(client_id)s
      AND processed_at > now() - make_interval(secs => %(window_seconds)s)
), used AS (
    SELECT coalesce(sum(seconds), 0)::bigint AS total FROM recent
), inserted AS (
    INSERT INTO public.training_video_usage (client_id, item_id, seconds)
    SELECT %(client_id)s, %(item_id)s, %(seconds)s
    WHERE NOT EXISTS (SELECT 1 FROM existing)
      AND (SELECT total FROM used) + %(seconds)s <= %(cap)s
    ON CONFLICT (client_id, item_id) DO NOTHING
    RETURNING item_id
), freed AS (
    SELECT processed_at,
           sum(seconds) OVER (ORDER BY processed_at, item_id) AS gone
    FROM recent
)
SELECT EXISTS (SELECT 1 FROM existing) OR EXISTS (SELECT 1 FROM inserted) AS reserved,
       coalesce(
           (SELECT processed_at + make_interval(secs => %(window_seconds)s)
            FROM freed
            WHERE (SELECT total FROM used) - gone + %(seconds)s <= %(cap)s
            ORDER BY processed_at
            LIMIT 1),
           now() + make_interval(secs => %(window_seconds)s)
       ) AS until;
