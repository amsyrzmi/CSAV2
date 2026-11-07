-- A NULL parameter leaves its column as it is.
UPDATE public.training_items
SET title = coalesce(%(title)s::text, title),
    description = coalesce(%(description)s::text, description),
    category = coalesce(%(category)s::text, category),
    view_mode = coalesce(%(view_mode)s::text, view_mode),
    updated_at = now()
WHERE client_id = %(client_id)s AND id = %(id)s
RETURNING id, title, description, category, view_mode, page_count, byte_size, created_at, created_by,
          summary IS NOT NULL AS has_summary;
