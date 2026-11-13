-- Every stored name the company's rows keep; "uploading" marks a video whose pieces are still
-- arriving, whose temporary ".<storage_name>.part" file the sweep must keep too. "refused" marks a
-- video failed for a reason in the file itself: its file is deleted at once, and when that delete
-- failed the sweep deletes it (the row stays, so the manager still sees why). A video failed for a
-- retryable reason keeps its file for the retry.
SELECT storage_name,
       status = 'uploading' AS uploading,
       coalesce(status = 'failed' AND failure_code IN
           ('training_video_unreadable', 'training_video_too_long', 'training_video_codec'),
           false) AS refused
FROM public.training_items
WHERE client_id = %s;
