-- Read in a plain tenant transaction, no lock: the summary path holds nothing across the PDF
-- parse or the model call. A video's summary is shown only when it is ready and has speech; for
-- any other video the summary column reads as empty, whatever it holds.
SELECT id, kind, status, storage_name, byte_size,
       CASE WHEN kind = 'pdf' OR (status = 'ready' AND speech = 'speech') THEN summary END AS summary,
       summary_truncated, summary_model, coalesce(summarised_at, updated_at) AS summarised_at
FROM public.training_items
WHERE client_id = %s AND id = %s;
