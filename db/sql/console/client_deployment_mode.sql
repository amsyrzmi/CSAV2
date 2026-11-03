SELECT deployment_mode, config -> 'policy' ->> 'deployment_mode' AS policy_mode
FROM public.clients WHERE id = %s;
