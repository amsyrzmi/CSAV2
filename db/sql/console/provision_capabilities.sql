INSERT INTO public.capability_registry
    (client_id, name, display_name, description, pattern, workflow_id, input_schema,
     is_enabled, client_enabled)
VALUES (%s, %s, %s, %s, 'python', %s, %s, false, false)
ON CONFLICT (client_id, name) DO NOTHING;
