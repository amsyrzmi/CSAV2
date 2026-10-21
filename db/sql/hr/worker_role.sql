SELECT current_user='csa_hr_maintenance' AND NOT rolsuper AND NOT rolbypassrls AS allowed FROM pg_roles WHERE rolname=current_user;
