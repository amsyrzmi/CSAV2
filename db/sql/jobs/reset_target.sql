SELECT json_build_object(
    'system_identifier', system_identifier::text,
    'database', current_database()
)::text AS identity
FROM pg_control_system();
