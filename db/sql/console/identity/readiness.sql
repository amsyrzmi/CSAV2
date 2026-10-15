SELECT current_user AS role,
NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
    AND c.relname NOT LIKE 'console_%'
    -- PostGIS extension dictionaries/views are public metadata, not CSA domain data.
    AND (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    OR NOT EXISTS (
        SELECT 1 FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid
        WHERE d.classid='pg_class'::regclass AND d.objid=c.oid
        AND d.refclassid='pg_extension'::regclass AND d.deptype='e'
        AND e.extname IN ('postgis', 'pg_trgm', 'uuid-ossp', 'vector')
    ))
    AND has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
) AS domain_isolated,
NOT has_table_privilege(current_user,'public.console_audit','UPDATE,DELETE,TRUNCATE') AS audit_immutable,
(SELECT NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication AND NOT rolinherit
 FROM pg_roles WHERE rolname=current_user) AS role_safe,
NOT EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.prosecdef
    AND has_function_privilege(current_user,p.oid,'EXECUTE')
) AS functions_isolated,
NOT EXISTS (
    SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
    WHERE r.rolname=current_user
) AS membership_isolated,
(SELECT bool_and(has_table_privilege(current_user,t,'SELECT'))
 FROM unnest(ARRAY['public.console_users','public.console_sessions','public.console_memberships',
                  'public.console_tokens','public.console_login_attempts','public.console_audit']) t
) AS identity_access;
