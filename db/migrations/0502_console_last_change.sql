-- "Last changed" line on the Services page (console redesign phase 2, owner decision 2 option B).
-- Adds public.console_last_change(p_client, p_target, p_actions): the single newest console_audit
--   row for one tenant, one target (package key or capability name) and a set of actions, with the
--   actor's display name and whether that actor is a platform admin (false when the actor row is
--   gone). csa_app may INSERT into console_audit but cannot SELECT it or read
--   console_users (0270), so the read goes through this narrow SECURITY DEFINER function instead of
--   widening any grant. It runs as the owner csa, which bypasses the non-forced RLS on
--   console_audit, so it filters by tenant itself: client_id = p_client, and p_client must equal the
--   transaction's app.client_id (the same tenant RLS would allow). It returns only action, time,
--   actor id, display name, audit details and the actor's platform-admin flag; never email, password
--   or session material.
-- Adds index console_audit_tenant_target_time (client_id, target_id, created_at DESC): the existing
--   console_audit_tenant_time (client_id, created_at DESC) cannot seek by target, so a target that
--   was never changed scanned the tenant's whole audit history (chat.queued and workspace rows
--   included). EXPLAIN on 200k synthetic rows: 36-76 ms per lookup without it, 0.2 ms with it.
-- No tables, policies or roles change. FORCE RLS stays off. EXECUTE goes to csa_app only.
-- Pre-check: 0270 applied (console_audit, console_users).
-- Safe to run twice: CREATE OR REPLACE FUNCTION, CREATE INDEX IF NOT EXISTS; ALTER OWNER, REVOKE
--   and GRANT are idempotent. Not CONCURRENTLY: the runner applies each file in a transaction.
-- Rollback: DROP FUNCTION public.console_last_change(uuid, text, text[]);
--           DROP INDEX public.console_audit_tenant_target_time;

CREATE INDEX IF NOT EXISTS console_audit_tenant_target_time
    ON public.console_audit (client_id, target_id, created_at DESC);

CREATE OR REPLACE FUNCTION public.console_last_change(p_client uuid, p_target text, p_actions text[])
RETURNS TABLE(action text, at timestamptz, actor_id uuid, actor_name text, details jsonb,
    actor_is_platform_admin boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT a.action, a.created_at, a.actor_id, u.display_name, a.details,
        coalesce(u.is_platform_admin, false)
    FROM public.console_audit a
    LEFT JOIN public.console_users u ON u.id = a.actor_id
    WHERE a.client_id = p_client
      AND p_client = nullif(pg_catalog.current_setting('app.client_id', true), '')::uuid
      AND a.target_id = p_target
      AND a.action = ANY (p_actions)
    ORDER BY a.created_at DESC
    LIMIT 1;
$$;

ALTER FUNCTION public.console_last_change(uuid, text, text[]) OWNER TO csa;
REVOKE ALL ON FUNCTION public.console_last_change(uuid, text, text[])
    FROM PUBLIC, csa_ro, csa_identity;
GRANT EXECUTE ON FUNCTION public.console_last_change(uuid, text, text[]) TO csa_app;
