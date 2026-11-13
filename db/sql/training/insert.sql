-- Adds a PDF (a video is added by its own upload start). A PDF has no job, so no job columns.
INSERT INTO public.training_items
    (id, client_id, kind, title, view_mode, storage_name, original_name, media_type, byte_size,
     sha256, page_count, created_by, request_key, fingerprint)
VALUES
    (%(id)s, %(client_id)s, 'pdf', %(title)s, %(view_mode)s, %(storage_name)s, %(original_name)s,
     %(media_type)s, %(byte_size)s, %(sha256)s, %(page_count)s, %(created_by)s, %(request_key)s,
     %(fingerprint)s)
RETURNING id, kind, title, description, category, view_mode, page_count, byte_size, created_at,
          created_by, status, duration_ms, failure_code, video_codec,
          summary IS NOT NULL AS has_summary;
