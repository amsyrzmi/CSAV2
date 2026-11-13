-- Video in the training library, part 2: the tables behind a video's upload, processing and search.
-- Adds, all with client_id (references clients), row security with the same tenant_isolation policy
--   as training_items (0541), ENABLE only, never FORCE; SELECT, INSERT, UPDATE, DELETE to csa_app;
--   nothing to PUBLIC, csa_ro or csa_identity (training data is not for csa_ro):
--   public.training_video_uploads: one row per video upload in progress (PK client_id, item_id):
--     the storage multipart upload id, the agreed piece size and count, the declared byte size, and
--     when the last piece arrived.
--   public.training_video_pieces: one row per piece received (PK client_id, item_id, number): size,
--     sha256 and the storage etag.
--   public.training_video_jobs: one row per video being processed (PK client_id, item_id): stage
--     (transcribing, summarising), status (queued, processing, done, failed), attempts, lock and
--     retry time, chunk progress, the partial results as jsonb, and an error code. Index
--     (client_id, status, next_attempt_at) serves the claim query.
--   public.training_transcript_segments: the speech-to-text output per audio chunk (PK client_id,
--     item_id, chunk_index, position), with start and end in milliseconds.
--   public.training_transcript_windows: the transcript regrouped into searchable windows, each with
--     a vector(384) embedding and the model that made it (PK client_id, item_id, position).
--   public.training_video_usage: one row per video processed, for the company's monthly allowance
--     (UNIQUE client_id, item_id; index client_id, processed_at). It has no foreign key to
--     training_items: deleting a video must not give its seconds back.
--   Every table but usage has a composite foreign key (client_id, item_id) to
--   training_items(client_id, id) ON DELETE CASCADE, so a row cannot point at another company's item.
-- No new roles and no FORCE ROW LEVEL SECURITY. Safe to run twice: IF NOT EXISTS, policies dropped
--   and made again.
-- Pre-check: 0600 applied (the unique key training_items_client_id_id_key must exist) and the vector
--   extension is installed (baseline). The tables are new and empty; nothing is rewritten.
-- Rollback (after the app no longer reads them; as the database owner):
--   DROP TABLE public.training_video_usage; (the monthly usage count is lost)
--   DROP TABLE public.training_transcript_windows, public.training_transcript_segments,
--     public.training_video_jobs, public.training_video_pieces, public.training_video_uploads;
--   Finally delete the schema_migrations row for 0601, or the runner thinks it is still applied.

CREATE TABLE IF NOT EXISTS public.training_video_uploads (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    item_id uuid NOT NULL,
    upload_id text,
    piece_size int NOT NULL CHECK (piece_size >= 5242880),
    piece_count int NOT NULL CHECK (piece_count BETWEEN 1 AND 10000),
    declared_bytes bigint NOT NULL CHECK (declared_bytes BETWEEN 1 AND 524288000),
    last_piece_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, item_id),
    FOREIGN KEY (client_id, item_id)
        REFERENCES public.training_items(client_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.training_video_pieces (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    item_id uuid NOT NULL,
    number int NOT NULL CHECK (number BETWEEN 1 AND 10000),
    byte_size int NOT NULL CHECK (byte_size BETWEEN 1 AND 8388608),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    etag text NOT NULL CHECK (char_length(etag) <= 128),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, item_id, number),
    FOREIGN KEY (client_id, item_id)
        REFERENCES public.training_items(client_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.training_video_jobs (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    item_id uuid NOT NULL,
    stage text NOT NULL DEFAULT 'transcribing' CHECK (stage IN ('transcribing', 'summarising')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'done', 'failed')),
    attempts int NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    locked_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    chunks_total int,
    chunks_done int NOT NULL DEFAULT 0,
    parts jsonb NOT NULL DEFAULT '[]',
    error_code text CHECK (char_length(error_code) <= 64),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, item_id),
    FOREIGN KEY (client_id, item_id)
        REFERENCES public.training_items(client_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS training_video_jobs_claim
    ON public.training_video_jobs (client_id, status, next_attempt_at);

CREATE TABLE IF NOT EXISTS public.training_transcript_segments (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    item_id uuid NOT NULL,
    chunk_index int NOT NULL,
    position int NOT NULL,
    start_ms int NOT NULL CHECK (start_ms >= 0),
    end_ms int NOT NULL,
    text text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 2000),
    PRIMARY KEY (client_id, item_id, chunk_index, position),
    FOREIGN KEY (client_id, item_id)
        REFERENCES public.training_items(client_id, id) ON DELETE CASCADE,
    CHECK (end_ms >= start_ms)
);

CREATE TABLE IF NOT EXISTS public.training_transcript_windows (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    item_id uuid NOT NULL,
    position int NOT NULL,
    start_ms int NOT NULL CHECK (start_ms >= 0),
    end_ms int NOT NULL,
    text text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 8000),
    embedding public.vector(384) NOT NULL,
    embed_model text NOT NULL,
    PRIMARY KEY (client_id, item_id, position),
    FOREIGN KEY (client_id, item_id)
        REFERENCES public.training_items(client_id, id) ON DELETE CASCADE,
    CHECK (end_ms >= start_ms)
);

CREATE TABLE IF NOT EXISTS public.training_video_usage (
    client_id uuid NOT NULL REFERENCES public.clients(id),
    item_id uuid NOT NULL,
    seconds int NOT NULL CHECK (seconds BETWEEN 1 AND 3600),
    processed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, item_id)
);
CREATE INDEX IF NOT EXISTS training_video_usage_processed
    ON public.training_video_usage (client_id, processed_at);

DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['training_video_uploads', 'training_video_pieces', 'training_video_jobs',
                           'training_transcript_segments', 'training_transcript_windows',
                           'training_video_usage'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON public.%I'
      ' USING (client_id = nullif(current_setting(''app.client_id'',true),'''')::uuid)'
      ' WITH CHECK (client_id = nullif(current_setting(''app.client_id'',true),'''')::uuid)', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, csa_ro, csa_identity', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO csa_app', t);
  END LOOP;
END $$;
