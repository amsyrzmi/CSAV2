-- Every stored name the company's rows keep; "uploading" marks a video whose pieces are still
-- arriving, whose temporary ".<storage_name>.part" file the sweep must keep too.
SELECT storage_name, status = 'uploading' AS uploading
FROM public.training_items
WHERE client_id = %s;
