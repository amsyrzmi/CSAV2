-- Every company, for the platform-admin billing overview, by name. public.clients is readable on
-- the app pool without a tenant (as console/client_list.sql); each company's usage is then read in
-- its own tx() for that company.
SELECT id, name FROM public.clients ORDER BY name, id;
