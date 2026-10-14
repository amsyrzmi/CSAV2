-- LG10: Scheduled jobs runner execution log.

CREATE TABLE IF NOT EXISTS public.job_runs (
    id bigserial PRIMARY KEY,
    job text NOT NULL,
    client_id uuid REFERENCES public.clients(id) ON DELETE CASCADE,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    ok boolean,
    stats jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text
);

CREATE INDEX IF NOT EXISTS job_runs_job_started ON public.job_runs (job, started_at DESC);
