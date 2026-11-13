-- Queue the uploaded video for processing (the training worker claims it). One job per item.
INSERT INTO public.training_video_jobs (client_id, item_id, stage, status)
VALUES (%(client_id)s, %(item_id)s, 'transcribing', 'queued')
ON CONFLICT (client_id, item_id) DO NOTHING
RETURNING stage AS job_stage, status AS job_status, chunks_done, chunks_total,
          error_code AS job_error;
