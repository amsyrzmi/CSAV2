-- HR processes, part 1: the tables for process templates, onboarding and offboarding.
-- Adds (every table has client_id referencing clients and composite keys on (client_id, id), so a
--   row can never point at another company's row; row security ENABLE only, never FORCE, with the
--   same tenant_isolation policy as media_assets (0610); REVOKE ALL from PUBLIC, csa_app, csa_ro,
--   csa_identity and csa_hr_maintenance, then SELECT to csa_app only. There is no write grant: every
--   write goes through the definer functions of later migrations (0631 onwards)):
--   public.hr_process_templates: one row per template (kind onboarding, offboarding or custom, name,
--     version, is_active). version starts at 1 and is raised by every edit (a later function).
--   public.hr_process_template_rules: at most one row per template; a NULL column means "any".
--     department_id references hr_departments, employment_type, legal_entity_reference and region
--     are text. A template with no rules row is the company default.
--   public.hr_process_template_steps: the steps of a template, unique by (template, ordinal).
--     owner_role is hr, manager, employee or person; owner_employee_id is required exactly when the
--     role is person. anchor is start_date, last_day or probation_end, due_offset_days is between
--     -365 and 365, approval_by is hr or manager, auto_check is account_linked,
--     personal_details_complete, access_removed or document_uploaded:<category>.
--   public.hr_processes: one row per started process; the steps are copied to hr_process_tasks when
--     it starts and never read back from the template. One active process per (employee, kind)
--     except for kind custom (partial unique index hr_processes_one_active).
--   public.hr_process_tasks: the frozen copy of the steps, with the resolved assignee, the due date
--     (NULL while its anchor date is not set), the status and the approval fields. Indexed by
--     (client_id, assignee_employee_id, status, due_on) and unique by (client_id, process_id,
--     ordinal).
--   public.hr_process_events: the log of everything that happens to a process, immutable through
--     hr_immutable_record (UPDATE, DELETE and TRUNCATE are refused for every role).
--   public.hr_employees.probation_end date NULL, the date a step anchored on probation_end counts
--     from. It is not part of the employee history trigger (hr_employee_record_change).
-- No new roles and no FORCE ROW LEVEL SECURITY. Safe to run twice: IF NOT EXISTS, policies and
--   triggers dropped and made again, grants and revokes repeat harmlessly.
-- Pre-check: 0521 applied (hr_immutable_record, hr_employees, hr_departments). The tables are new
--   and empty; the only existing table touched is hr_employees (one nullable column, no rewrite).
-- Rollback (as the database owner, after the application no longer reads the tables):
--   ALTER TABLE public.hr_employees DROP COLUMN probation_end;  (the dates are lost)
--   DROP TABLE public.hr_process_events, public.hr_process_tasks, public.hr_processes,
--     public.hr_process_template_steps, public.hr_process_template_rules,
--     public.hr_process_templates;  (every template, process, task and event is lost; take a dump
--     first)
--   Later migrations (0631 onwards) add functions that use these tables: roll those back first.
--   Finally delete the schema_migrations row for 0630, or the runner thinks it is still applied.

ALTER TABLE public.hr_employees ADD COLUMN IF NOT EXISTS probation_end date;

CREATE TABLE IF NOT EXISTS public.hr_process_templates(
 client_id uuid NOT NULL REFERENCES public.clients(id),
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 kind text NOT NULL CHECK(kind IN ('onboarding','offboarding','custom')),
 name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 120),
 version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 is_active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(client_id,id));

CREATE TABLE IF NOT EXISTS public.hr_process_template_rules(
 client_id uuid NOT NULL REFERENCES public.clients(id),
 template_id uuid NOT NULL,
 department_id uuid,
 employment_type text,
 legal_entity_reference text,
 region text,
 PRIMARY KEY(client_id,template_id),
 FOREIGN KEY(client_id,template_id) REFERENCES public.hr_process_templates(client_id,id),
 FOREIGN KEY(client_id,department_id) REFERENCES public.hr_departments(client_id,id));

CREATE TABLE IF NOT EXISTS public.hr_process_template_steps(
 client_id uuid NOT NULL REFERENCES public.clients(id),
 template_id uuid NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal>=1),
 title_en text NOT NULL,
 title_ms text,
 description_en text,
 description_ms text,
 owner_role text NOT NULL CHECK(owner_role IN ('hr','manager','employee','person')),
 owner_employee_id uuid,
 anchor text NOT NULL CHECK(anchor IN ('start_date','last_day','probation_end')),
 due_offset_days integer NOT NULL CHECK(due_offset_days BETWEEN -365 AND 365),
 required boolean NOT NULL DEFAULT true,
 approval_by text CHECK(approval_by IN ('hr','manager')),
 auto_check text CHECK(auto_check IN ('account_linked','personal_details_complete','access_removed')
  OR (auto_check LIKE 'document\_uploaded:%' AND length(auto_check)>length('document_uploaded:'))),
 reference_note text,
 PRIMARY KEY(client_id,template_id,ordinal),
 FOREIGN KEY(client_id,template_id) REFERENCES public.hr_process_templates(client_id,id),
 FOREIGN KEY(client_id,owner_employee_id) REFERENCES public.hr_employees(client_id,id),
 CHECK((owner_role='person')=(owner_employee_id IS NOT NULL)));

CREATE TABLE IF NOT EXISTS public.hr_processes(
 client_id uuid NOT NULL REFERENCES public.clients(id),
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 employee_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('onboarding','offboarding','custom')),
 template_id uuid NOT NULL,
 template_version integer NOT NULL CHECK(template_version>=1),
 anchor_dates jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(anchor_dates)='object'),
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','completed','cancelled')),
 started_by uuid REFERENCES public.console_users(id),
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 closed_at timestamptz,
 PRIMARY KEY(client_id,id),
 FOREIGN KEY(client_id,employee_id) REFERENCES public.hr_employees(client_id,id),
 FOREIGN KEY(client_id,template_id) REFERENCES public.hr_process_templates(client_id,id));
CREATE UNIQUE INDEX IF NOT EXISTS hr_processes_one_active
 ON public.hr_processes(client_id,employee_id,kind) WHERE status='active' AND kind<>'custom';
CREATE INDEX IF NOT EXISTS hr_processes_by_employee
 ON public.hr_processes(client_id,employee_id,started_at DESC);

CREATE TABLE IF NOT EXISTS public.hr_process_tasks(
 client_id uuid NOT NULL REFERENCES public.clients(id),
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 process_id uuid NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal>=1),
 title_en text NOT NULL,
 title_ms text,
 description_en text,
 description_ms text,
 owner_role text NOT NULL CHECK(owner_role IN ('hr','manager','employee','person')),
 assignee_employee_id uuid,
 anchor text NOT NULL CHECK(anchor IN ('start_date','last_day','probation_end')),
 due_offset_days integer NOT NULL CHECK(due_offset_days BETWEEN -365 AND 365),
 due_on date,
 due_overridden boolean NOT NULL DEFAULT false,
 required boolean NOT NULL DEFAULT true,
 approval_by text CHECK(approval_by IN ('hr','manager')),
 auto_check text CHECK(auto_check IN ('account_linked','personal_details_complete','access_removed')
  OR (auto_check LIKE 'document\_uploaded:%' AND length(auto_check)>length('document_uploaded:'))),
 status text NOT NULL DEFAULT 'todo'
  CHECK(status IN ('todo','awaiting_approval','done','rejected','skipped')),
 completed_by uuid REFERENCES public.console_users(id),
 completed_at timestamptz,
 approved_by uuid REFERENCES public.console_users(id),
 approved_at timestamptz,
 note text,
 version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 PRIMARY KEY(client_id,id),
 FOREIGN KEY(client_id,process_id) REFERENCES public.hr_processes(client_id,id),
 FOREIGN KEY(client_id,assignee_employee_id) REFERENCES public.hr_employees(client_id,id));
CREATE UNIQUE INDEX IF NOT EXISTS hr_process_tasks_by_process
 ON public.hr_process_tasks(client_id,process_id,ordinal);
CREATE INDEX IF NOT EXISTS hr_process_tasks_by_assignee
 ON public.hr_process_tasks(client_id,assignee_employee_id,status,due_on);

CREATE TABLE IF NOT EXISTS public.hr_process_events(
 client_id uuid NOT NULL REFERENCES public.clients(id),
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 process_id uuid NOT NULL,
 task_id uuid,
 at timestamptz NOT NULL DEFAULT clock_timestamp(),
 actor uuid REFERENCES public.console_users(id),
 action text NOT NULL CHECK(length(action) BETWEEN 1 AND 80),
 detail jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(detail)='object'),
 PRIMARY KEY(client_id,id),
 FOREIGN KEY(client_id,process_id) REFERENCES public.hr_processes(client_id,id),
 FOREIGN KEY(client_id,task_id) REFERENCES public.hr_process_tasks(client_id,id));
CREATE INDEX IF NOT EXISTS hr_process_events_by_process
 ON public.hr_process_events(client_id,process_id,at);

DROP TRIGGER IF EXISTS hr_process_events_immutable ON public.hr_process_events;
CREATE TRIGGER hr_process_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
 ON public.hr_process_events FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['hr_process_templates','hr_process_template_rules',
   'hr_process_template_steps','hr_processes','hr_process_tasks','hr_process_events'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I',t);
  EXECUTE format('CREATE POLICY tenant_isolation ON public.%I'
   ' USING (client_id=nullif(current_setting(''app.client_id'',true),'''')::uuid)'
   ' WITH CHECK (client_id=nullif(current_setting(''app.client_id'',true),'''')::uuid)',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance',t);
  EXECUTE format('GRANT SELECT ON public.%I TO csa_app',t);
 END LOOP;
END $$;
