SELECT current_user = 'csa_app' AND NOT rolsuper AND NOT rolbypassrls AS safe_role,
    (SELECT count(*) = 27 AND bool_and(c.relrowsecurity AND NOT c.relforcerowsecurity)
     FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relkind='r' AND c.relname = ANY(ARRAY[
         'hr_employees','hr_employee_accounts','hr_client_owners','hr_grants',
         'hr_training_requests','hr_training_receipts','hr_training_audit',
         'hr_agent_settings','hr_agent_confirmations','hr_workflow_grants','hr_cases','hr_case_events','hr_private_grants','hr_operations','hr_candidates','hr_interviews',
         'hr_calendars','hr_leave_balances','hr_leave_usage','hr_shifts','hr_payroll_rules',
         'hr_payroll_snapshots','hr_notifications','hr_chat_plans','hr_automation_settings','hr_maintenance_runs','hr_authority_events']))
    AND to_regprocedure('public.hr_lock_session(uuid,uuid,uuid,text,integer,text)') IS NOT NULL
    AND to_regprocedure('public.hr_lock_authority(uuid,uuid)') IS NOT NULL
    AND to_regprocedure('public.hr_lock_employee(uuid,uuid)') IS NOT NULL
    AND to_regprocedure('public.hr_resource_visible(uuid,uuid,text)') IS NOT NULL
    AND to_regprocedure('public.hr_guard_training_transition()') IS NOT NULL
    AND to_regprocedure('public.hr_require_training_audit()') IS NOT NULL
    AND to_regprocedure('public.hr_immutable_record()') IS NOT NULL
    AND to_regprocedure('public.hr_lock_agent_switch(uuid,text)') IS NOT NULL
    AND to_regprocedure('public.hr_case_access(uuid,text,text,uuid)') IS NOT NULL
    AND to_regprocedure('public.hr_admin_apply(uuid,uuid,text,jsonb)') IS NOT NULL
    AND to_regprocedure('public.hr_private_access(uuid,text)') IS NOT NULL
    AND to_regprocedure('public.hr_maintain_due(uuid)') IS NOT NULL
    AND to_regprocedure('public.hr_employment_matches(uuid,uuid,text,text,text)') IS NOT NULL
    AND to_regprocedure('public.hr_calendar_lock(uuid,text,integer)') IS NOT NULL
    AS schema_ready
FROM pg_roles WHERE rolname=current_user;
