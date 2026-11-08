-- Read in a plain tenant transaction, no lock: the summary path holds nothing across the PDF
-- parse or the model call.
SELECT id, storage_name, byte_size, summary, summary_truncated, summary_model,
       coalesce(summarised_at, updated_at) AS summarised_at
FROM public.training_items
WHERE client_id = %s AND id = %s;
