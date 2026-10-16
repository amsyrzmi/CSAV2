INSERT INTO public.kb_ingest_jobs(id,client_id,source,files,meta,requested_by,created_at)
VALUES (%s,%s,'upload',%s,%s,%s,clock_timestamp());
