-- 0120_resolve_endpoint.sql — pre-tenant endpoint lookup + approvals uniqueness
-- (LG01 §5.4, §1/§12.6).
--
-- resolve_endpoint is the single sanctioned SECURITY DEFINER function that reads
-- webhook_endpoints across tenants BEFORE app.client_id is set (gateway ingress
-- routing). It must be OWNED BY csa so the definer bypass works under non-FORCE
-- RLS; the explicit ALTER FUNCTION ... OWNER TO csa makes that invariant hold
-- regardless of which role runs the migration. It returns only non-secret
-- routing fields, never tokens.

CREATE OR REPLACE FUNCTION public.resolve_endpoint(p_public_id text)
RETURNS TABLE(
  endpoint_id uuid,
  client_id uuid,
  credential_id uuid,
  reply_mode text,
  sync_wait_ms int,
  is_enabled boolean,
  disabled_reason text
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT id, client_id, credential_id, reply_mode, sync_wait_ms, is_enabled, disabled_reason
  FROM webhook_endpoints
  WHERE public_id = p_public_id;
$$;

ALTER FUNCTION public.resolve_endpoint(text) OWNER TO csa;
REVOKE ALL ON FUNCTION public.resolve_endpoint(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.resolve_endpoint(text) TO csa_app;

-- Additive UNIQUE(trace_id) on approvals: trace_id doubles as the LangGraph
-- thread id, so it must be unique. Fail LOUDLY if duplicates already exist
-- (cleanup is deferred to LG11); do NOT silently proceed.
DO $$
DECLARE
  dup_count bigint;
BEGIN
  SELECT count(*) INTO dup_count
  FROM (
    SELECT trace_id
    FROM public.approvals
    GROUP BY trace_id
    HAVING count(*) > 1
  ) d;

  IF dup_count > 0 THEN
    RAISE EXCEPTION
      'approvals has % trace_id value(s) with duplicates; cannot add UNIQUE(trace_id). '
      'Resolve duplicates before migrating (cleanup deferred to LG11).', dup_count;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'approvals_trace_id_key'
      AND conrelid = 'public.approvals'::regclass
  ) THEN
    ALTER TABLE public.approvals ADD CONSTRAINT approvals_trace_id_key UNIQUE (trace_id);
  END IF;
END $$;

-- The new UNIQUE constraint supersedes the redundant non-unique index.
DROP INDEX IF EXISTS public.idx_approvals_trace;
