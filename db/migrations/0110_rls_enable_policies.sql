-- 0110_rls_enable_policies.sql — staged RLS, ENABLE without FORCE (LG01 §5.3).
--
-- The target set is derived from the live schema (every public base table with
-- a client_id column) MINUS two structural exclusions, so it cannot drift from
-- a hand-maintained list:
--   clients                      — tenant root; id IS the tenant key, no client_id
--   outbound_delivery_attempts   — child of outbound_deliveries, no client_id
--
-- ENABLE (not FORCE): owner csa bypasses policies (n8n, migrations, SECURITY
-- DEFINER triggers keep working); csa_app / csa_ro are policy-subject. FORCE is
-- deferred to LG11. The loop skips tables that already have RLS enabled, so a
-- re-run is a no-op (idempotent).

DO $$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT c.table_name
    FROM information_schema.columns c
    JOIN pg_class pc ON pc.relname = c.table_name
    JOIN pg_namespace pn ON pn.oid = pc.relnamespace AND pn.nspname = 'public'
    WHERE c.table_schema = 'public'
      AND c.column_name = 'client_id'
      AND pc.relkind = 'r'                       -- ordinary tables only, not views
      AND c.table_name NOT IN ('clients', 'outbound_delivery_attempts')
      AND pc.relrowsecurity = false              -- skip already-enabled tables
    ORDER BY c.table_name
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.table_name);
    -- Guard the policy create so a re-run after a partial apply is safe.
    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname = 'public' AND tablename = r.table_name
        AND policyname = 'tenant_isolation'
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON public.%I '
        'USING (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid) '
        'WITH CHECK (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid)',
        r.table_name
      );
    END IF;
  END LOOP;
END $$;

-- Set security_invoker=true on the views csa_app reads so base-table RLS is
-- evaluated as the invoking role (csa_app), not the view owner (§1.4). csa_app
-- already holds SELECT on every referenced base table and EXECUTE on every
-- referenced function (0100_roles.sql), so the invoker read path is satisfied.
ALTER VIEW public.webhook_credentials      SET (security_invoker = true);
ALTER VIEW public.price_book               SET (security_invoker = true);
ALTER VIEW public.quote_catalog            SET (security_invoker = true);
ALTER VIEW public.v_booking_schedule       SET (security_invoker = true);
ALTER VIEW public.v_invoice_status         SET (security_invoker = true);
ALTER VIEW public.v_order_status           SET (security_invoker = true);
ALTER VIEW public.v_product_catalog        SET (security_invoker = true);
ALTER VIEW public.v_store_unserved_demand  SET (security_invoker = true);
ALTER VIEW public.v_warranty_eligibility   SET (security_invoker = true);
