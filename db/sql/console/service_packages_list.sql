-- csa_app has no grant on console_users; actors are returned as ids.
SELECT * FROM public.console_service_packages WHERE client_id=%s;
