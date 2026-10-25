SELECT current_user='csa_app' AND NOT rolsuper AND NOT rolbypassrls AS safe_role,
 (SELECT count(*)=6 FROM information_schema.tables WHERE table_schema='public' AND table_name IN
  ('email_settings','email_mailboxes','email_oauth_states','email_threads','email_drafts','email_receipts')) AS schema_ready
FROM pg_roles WHERE rolname=current_user;
