-- One picture per (company, kind, subject). Replacing keeps the row id and changes sha256 (the
-- version). `overwrite` false (a generated picture) leaves an existing row untouched and returns
-- no row. `inserted` is true for a new row.
INSERT INTO public.media_assets
    (client_id, kind, subject_key, media_type, byte_size, width, height, sha256, source, created_by)
VALUES (%(client_id)s, %(kind)s, %(subject_key)s, %(media_type)s, %(byte_size)s, %(width)s,
        %(height)s, %(sha256)s, %(source)s, %(created_by)s)
ON CONFLICT (client_id, kind, subject_key) DO UPDATE
SET media_type = EXCLUDED.media_type,
    byte_size = EXCLUDED.byte_size,
    width = EXCLUDED.width,
    height = EXCLUDED.height,
    sha256 = EXCLUDED.sha256,
    source = EXCLUDED.source,
    created_by = EXCLUDED.created_by,
    updated_at = now()
WHERE %(overwrite)s
RETURNING id, sha256, (xmax = 0) AS inserted;
