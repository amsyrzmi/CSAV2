-- HR processes, part 4: reading processes and tasks, and who sees what (spec 5.3).
-- Adds (csa_app still has no privilege on the 0630 tables; these definer functions decide what each
--   caller sees, and return no rows or NULL for anything else, never an error and never another
--   tenant's data: p_client must be app.client_id, an id of another tenant is "not found"):
--   Who is asking (internal hr_process_caller(p_client)): the HR team is the HR owner or a
--     people.manage holder (hr_people_viewer = 'hr'); the caller's employee is the active employee
--     linked to app.hr_user_id in hr_employee_accounts (a person who has left has none). A manager
--     is the employee named as hr_employees.manager_id of the process's person; an assignee is the
--     employee named as a task's assignee_employee_id. Anyone else (no link, read-only member,
--     tenant admin without HR authority) sees nothing. No console switch is read.
--   What each one sees:
--     HR team: every process (also of people who have left), every task with notes, actors and
--       due-date fields, every event with its actor and detail, the anchor dates.
--     Manager (processes of direct reports): process name, kind, status, dates and progress; per
--       task only title, owner role, assignee display name, due date, status, required and overdue.
--       No notes, no descriptions, no completed_by or approved_by, no events, no anchor dates.
--     Assignee: their own tasks (with description and note, without actors), the process name and
--       kind and the person's display name; not the other tasks, not the person's id.
--   A task is open while its status is not done or skipped; it is overdue when it is open, its
--   process is active and due_on is before today in Asia/Kuala_Lumpur (as hr_leavers_due). A task
--   with no due date is never overdue (spec 5.7.3). The HR pool is the open tasks with no assignee
--   (hr tasks, and manager tasks whose manager could not be resolved, 0632).
--   public.hr_process_rows(p_client,p_kind,p_status,p_overdue,p_department,p_owner,p_limit,p_after):
--     the processes the HR team or a manager may see, newest first (started_at, id), filtered by
--     kind, status, overdue only (p_overdue true), the person's department, and owner (a person
--     with any task of the process assigned to them); NULL filters are off. Keyset paging: p_after
--     is the last process id of the previous page (an unknown id gives no rows); p_limit is clamped
--     to 0..201 (a page of 200 plus one to tell it was cut). Columns: process_id, employee_id,
--     employee_name, department_id, kind, name (the template's name, NULL without one), status,
--     started_at, closed_at, tasks_total, tasks_done (done or skipped), next_due_on (earliest open
--     due date), overdue.
--   public.hr_process_get(p_client,p_process) RETURNS jsonb: {view: hr | manager | assignee,
--     process, tasks, events (HR team only)}, in the shapes above; NULL when the caller may not see
--     the process.
--   public.hr_task_rows_mine(p_client,p_status,p_limit,p_after): the tasks assigned to the caller's
--     employee plus, for the HR team, the HR pool. p_status NULL means the open tasks, else one
--     status. Soonest due first (due_on, no date last, then id), keyset paged by the last task id
--     like hr_process_rows. Columns: task_id, process_id, process_kind, process_name,
--     employee_name, ordinal, title_en, title_ms, description_en, description_ms, owner_role, due_on,
--     required, approval_by, auto_check, status, note, version, overdue, pool.
--   public.hr_person_processes(p_client,p_employee) RETURNS jsonb: {view, employee_id, processes:
--     [{process_id, kind, name, status, started_at, closed_at, tasks_total, tasks_done, next_task:
--     {title_en, title_ms, due_on} | null}] newest first} for the HR team and the person's manager;
--     the HR team also gets anchor_dates {start_date, last_day, probation_end} and
--     no_matching_template {onboarding, offboarding}: true when the person has no process of that
--     kind and hr_process_match finds no template (called only after the authority check, with the
--     verified tenant). NULL for anyone else and for an id that is not a person of this tenant.
--   Internal helpers, no grant to any application role: hr_process_caller, hr_process_summary.
--   The four reads: EXECUTE to csa_app only.
-- No table, column or constraint change. Safe to run twice (CREATE OR REPLACE).
-- Pre-check: 0630, 0631 and 0632 applied.
-- Rollback (as the database owner, after the application no longer calls them):
--   DROP FUNCTION public.hr_person_processes(uuid,uuid);
--   DROP FUNCTION public.hr_task_rows_mine(uuid,text,integer,uuid);
--   DROP FUNCTION public.hr_process_get(uuid,uuid);
--   DROP FUNCTION public.hr_process_rows(uuid,text,text,boolean,uuid,uuid,integer,uuid);
--   DROP FUNCTION public.hr_process_summary(uuid,uuid); DROP FUNCTION public.hr_process_caller(uuid);
--   then delete the schema_migrations row for 0633. Nothing is written by these functions.

-- team: HR owner or people.manage holder. employee: the caller's active linked employee, or NULL.
-- Both false and NULL without an HR user or for another tenant than app.client_id.
CREATE OR REPLACE FUNCTION public.hr_process_caller(p_client uuid, OUT team boolean, OUT employee uuid)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE me uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
BEGIN
 team:=false;
 IF me IS NULL OR p_client IS NULL
  OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN RETURN; END IF;
 team:=coalesce(public.hr_people_viewer(p_client)='hr',false);
 SELECT a.employee_id INTO employee
 FROM public.hr_employee_accounts a
 JOIN public.hr_employees e ON e.client_id=a.client_id AND e.id=a.employee_id AND e.is_active
 WHERE a.client_id=p_client AND a.user_id=me;
END $$;

-- One process as the person profile shows it: progress and the next open task.
CREATE OR REPLACE FUNCTION public.hr_process_summary(p_client uuid, p_process uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT jsonb_build_object('process_id',p.id,'kind',p.kind,'name',t.name,'status',p.status,
  'started_at',p.started_at,'closed_at',p.closed_at,
  'tasks_total',(SELECT count(*) FROM public.hr_process_tasks x
                 WHERE x.client_id=p.client_id AND x.process_id=p.id),
  'tasks_done',(SELECT count(*) FROM public.hr_process_tasks x
                WHERE x.client_id=p.client_id AND x.process_id=p.id AND x.status IN ('done','skipped')),
  'next_task',(SELECT jsonb_build_object('title_en',x.title_en,'title_ms',x.title_ms,'due_on',x.due_on)
               FROM public.hr_process_tasks x
               WHERE x.client_id=p.client_id AND x.process_id=p.id
                AND x.status NOT IN ('done','skipped')
               ORDER BY coalesce(x.due_on,'infinity'::date), x.ordinal
               LIMIT 1))
 FROM public.hr_processes p
 LEFT JOIN public.hr_process_templates t ON t.client_id=p.client_id AND t.id=p.template_id
 WHERE p.client_id=p_client AND p.id=p_process
$$;

CREATE OR REPLACE FUNCTION public.hr_process_rows(p_client uuid, p_kind text, p_status text,
    p_overdue boolean, p_department uuid, p_owner uuid, p_limit integer, p_after uuid)
RETURNS TABLE(process_id uuid, employee_id uuid, employee_name text, department_id uuid, kind text,
    name text, status text, started_at timestamptz, closed_at timestamptz, tasks_total integer,
    tasks_done integer, next_due_on date, overdue boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 who record; today date:=(now() AT TIME ZONE 'Asia/Kuala_Lumpur')::date; after_at timestamptz;
BEGIN
 SELECT * INTO who FROM public.hr_process_caller(p_client) c;
 IF NOT who.team AND who.employee IS NULL THEN RETURN; END IF;
 IF p_after IS NOT NULL THEN
  SELECT p.started_at INTO after_at FROM public.hr_processes p
   WHERE p.client_id=p_client AND p.id=p_after;
  IF NOT FOUND THEN RETURN; END IF;
 END IF;
 RETURN QUERY
 SELECT p.id, e.id, e.display_name, e.department_id, p.kind, t.name, p.status, p.started_at,
  p.closed_at, k.total_n, k.done_n, k.next_due, k.late
 FROM public.hr_processes p
 JOIN public.hr_employees e ON e.client_id=p.client_id AND e.id=p.employee_id
 LEFT JOIN public.hr_process_templates t ON t.client_id=p.client_id AND t.id=p.template_id
 CROSS JOIN LATERAL (
  SELECT count(*)::integer AS total_n,
   count(*) FILTER (WHERE x.status IN ('done','skipped'))::integer AS done_n,
   min(x.due_on) FILTER (WHERE x.status NOT IN ('done','skipped')) AS next_due,
   coalesce(bool_or(p.status='active' AND x.status NOT IN ('done','skipped') AND x.due_on<today),
            false) AS late
  FROM public.hr_process_tasks x WHERE x.client_id=p.client_id AND x.process_id=p.id) k
 WHERE p.client_id=p_client
  AND (who.team OR e.manager_id=who.employee)
  AND (p_kind IS NULL OR p.kind=p_kind)
  AND (p_status IS NULL OR p.status=p_status)
  AND (p_overdue IS NOT TRUE OR k.late)
  AND (p_department IS NULL OR e.department_id=p_department)
  AND (p_owner IS NULL OR EXISTS(
   SELECT 1 FROM public.hr_process_tasks o
   WHERE o.client_id=p.client_id AND o.process_id=p.id AND o.assignee_employee_id=p_owner))
  AND (p_after IS NULL OR (p.started_at,p.id)<(after_at,p_after))
 ORDER BY p.started_at DESC, p.id DESC
 LIMIT least(greatest(coalesce(p_limit,0),0),201);
END $$;

CREATE OR REPLACE FUNCTION public.hr_process_get(p_client uuid, p_process uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 who record; p public.hr_processes%ROWTYPE; e public.hr_employees%ROWTYPE; viewer text; tname text;
 header jsonb; today date:=(now() AT TIME ZONE 'Asia/Kuala_Lumpur')::date; result jsonb;
BEGIN
 SELECT * INTO who FROM public.hr_process_caller(p_client) c;
 IF NOT who.team AND who.employee IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO p FROM public.hr_processes x WHERE x.client_id=p_client AND x.id=p_process;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO e FROM public.hr_employees x WHERE x.client_id=p_client AND x.id=p.employee_id;
 IF who.team THEN viewer:='hr';
 ELSIF e.manager_id=who.employee THEN viewer:='manager';
 ELSIF EXISTS(SELECT 1 FROM public.hr_process_tasks x
              WHERE x.client_id=p_client AND x.process_id=p.id
               AND x.assignee_employee_id=who.employee) THEN viewer:='assignee';
 ELSE RETURN NULL;
 END IF;
 SELECT t.name INTO tname FROM public.hr_process_templates t
  WHERE t.client_id=p_client AND t.id=p.template_id;
 header:=jsonb_build_object('process_id',p.id,'kind',p.kind,'name',tname,'status',p.status,
  'employee_name',e.display_name);
 IF viewer IN ('hr','manager') THEN
  header:=header||jsonb_build_object('employee_id',p.employee_id,'started_at',p.started_at,
   'closed_at',p.closed_at,
   'tasks_total',(SELECT count(*) FROM public.hr_process_tasks x
                  WHERE x.client_id=p_client AND x.process_id=p.id),
   'tasks_done',(SELECT count(*) FROM public.hr_process_tasks x
                 WHERE x.client_id=p_client AND x.process_id=p.id
                  AND x.status IN ('done','skipped')));
 END IF;
 IF viewer='hr' THEN
  header:=header||jsonb_build_object('template_id',p.template_id,'template_version',p.template_version,
   'anchor_dates',p.anchor_dates,'started_by',p.started_by);
 END IF;
 -- Three task shapes, each built on the one before: what a manager may see of anyone's task, what
 -- an assignee sees of their own, and the HR team's whole row.
 result:=jsonb_build_object('view',viewer,'process',header,'tasks',
  (SELECT coalesce(jsonb_agg(CASE WHEN viewer='hr' THEN s.own||s.hr_only
                                  WHEN s.mine THEN s.own
                                  ELSE s.base END ORDER BY s.ordinal),'[]'::jsonb)
   FROM (
    SELECT b.ordinal, b.mine, b.base, own.own, hr_only.hr_only
    FROM public.hr_process_tasks x
    LEFT JOIN public.hr_employees a ON a.client_id=x.client_id AND a.id=x.assignee_employee_id
    CROSS JOIN LATERAL (
     SELECT x.ordinal, coalesce(x.assignee_employee_id=who.employee,false) AS mine,
      jsonb_build_object('task_id',x.id,'ordinal',x.ordinal,'title_en',x.title_en,
       'title_ms',x.title_ms,'owner_role',x.owner_role,'assignee_name',a.display_name,
       'due_on',x.due_on,'status',x.status,'required',x.required,
       'overdue',coalesce(p.status='active' AND x.status NOT IN ('done','skipped')
                          AND x.due_on<today,false),
       'mine',coalesce(x.assignee_employee_id=who.employee,false)) AS base) b
    CROSS JOIN LATERAL (
     SELECT b.base||jsonb_build_object('description_en',x.description_en,
      'description_ms',x.description_ms,'anchor',x.anchor,'approval_by',x.approval_by,
      'auto_check',x.auto_check,'completed_at',x.completed_at,'note',x.note,
      'version',x.version) AS own) own
    CROSS JOIN LATERAL (
     SELECT jsonb_build_object('assignee_employee_id',x.assignee_employee_id,
      'due_offset_days',x.due_offset_days,'due_overridden',x.due_overridden,
      'completed_by',x.completed_by,'approved_by',x.approved_by,'approved_at',x.approved_at)
      AS hr_only) hr_only
    WHERE x.client_id=p_client AND x.process_id=p.id
     AND (viewer<>'assignee' OR x.assignee_employee_id=who.employee)) s));
 IF viewer='hr' THEN
  result:=result||jsonb_build_object('events',
   (SELECT coalesce(jsonb_agg(jsonb_build_object('event_id',v.id,'at',v.at,'actor',v.actor,
      'action',v.action,'task_id',v.task_id,'detail',v.detail) ORDER BY v.at, v.id),'[]'::jsonb)
    FROM public.hr_process_events v WHERE v.client_id=p_client AND v.process_id=p.id));
 END IF;
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION public.hr_task_rows_mine(p_client uuid, p_status text, p_limit integer,
    p_after uuid)
RETURNS TABLE(task_id uuid, process_id uuid, process_kind text, process_name text,
    employee_name text, ordinal integer, title_en text, title_ms text, description_en text,
    description_ms text, owner_role text, due_on date, required boolean, approval_by text,
    auto_check text, status text, note text, version integer, overdue boolean, pool boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 who record; today date:=(now() AT TIME ZONE 'Asia/Kuala_Lumpur')::date; after_due date;
BEGIN
 SELECT * INTO who FROM public.hr_process_caller(p_client) c;
 IF NOT who.team AND who.employee IS NULL THEN RETURN; END IF;
 IF p_after IS NOT NULL THEN
  SELECT coalesce(x.due_on,'infinity'::date) INTO after_due FROM public.hr_process_tasks x
   WHERE x.client_id=p_client AND x.id=p_after;
  IF NOT FOUND THEN RETURN; END IF;
 END IF;
 RETURN QUERY
 SELECT x.id, p.id, p.kind, t.name, e.display_name, x.ordinal, x.title_en, x.title_ms,
  x.description_en, x.description_ms, x.owner_role, x.due_on, x.required, x.approval_by,
  x.auto_check, x.status, x.note, x.version,
  coalesce(p.status='active' AND x.status NOT IN ('done','skipped') AND x.due_on<today,false),
  x.assignee_employee_id IS NULL
 FROM public.hr_process_tasks x
 JOIN public.hr_processes p ON p.client_id=x.client_id AND p.id=x.process_id
 JOIN public.hr_employees e ON e.client_id=p.client_id AND e.id=p.employee_id
 LEFT JOIN public.hr_process_templates t ON t.client_id=p.client_id AND t.id=p.template_id
 WHERE x.client_id=p_client
  AND (x.assignee_employee_id=who.employee OR (who.team AND x.assignee_employee_id IS NULL))
  AND CASE WHEN p_status IS NULL THEN x.status NOT IN ('done','skipped') ELSE x.status=p_status END
  AND (p_after IS NULL OR (coalesce(x.due_on,'infinity'::date),x.id)>(after_due,p_after))
 ORDER BY coalesce(x.due_on,'infinity'::date), x.id
 LIMIT least(greatest(coalesce(p_limit,0),0),201);
END $$;

CREATE OR REPLACE FUNCTION public.hr_person_processes(p_client uuid, p_employee uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE who record; e public.hr_employees%ROWTYPE; viewer text; result jsonb;
BEGIN
 SELECT * INTO who FROM public.hr_process_caller(p_client) c;
 IF NOT who.team AND who.employee IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO e FROM public.hr_employees x WHERE x.client_id=p_client AND x.id=p_employee;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF who.team THEN viewer:='hr';
 ELSIF e.manager_id=who.employee THEN viewer:='manager';
 ELSE RETURN NULL;
 END IF;
 result:=jsonb_build_object('view',viewer,'employee_id',e.id,'processes',
  (SELECT coalesce(jsonb_agg(public.hr_process_summary(p_client,p.id) ORDER BY p.started_at DESC, p.id DESC),
                   '[]'::jsonb)
   FROM public.hr_processes p WHERE p.client_id=p_client AND p.employee_id=e.id));
 IF viewer='hr' THEN
  -- hr_process_match is not tenant-checked: it is reached only here, after the authority check,
  -- with the verified tenant.
  result:=result||jsonb_build_object(
   'anchor_dates',jsonb_build_object('start_date',e.start_date,'last_day',e.last_day,
    'probation_end',e.probation_end),
   'no_matching_template',jsonb_build_object(
    'onboarding',NOT EXISTS(SELECT 1 FROM public.hr_processes p
                            WHERE p.client_id=p_client AND p.employee_id=e.id AND p.kind='onboarding')
     AND public.hr_process_match(p_client,e.id,'onboarding') IS NULL,
    'offboarding',NOT EXISTS(SELECT 1 FROM public.hr_processes p
                             WHERE p.client_id=p_client AND p.employee_id=e.id AND p.kind='offboarding')
     AND public.hr_process_match(p_client,e.id,'offboarding') IS NULL));
 END IF;
 RETURN result;
END $$;

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_process_caller(uuid)'::regprocedure,
  'public.hr_process_summary(uuid,uuid)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance',f);
 END LOOP;
 FOREACH f IN ARRAY ARRAY[
  'public.hr_process_rows(uuid,text,text,boolean,uuid,uuid,integer,uuid)'::regprocedure,
  'public.hr_process_get(uuid,uuid)'::regprocedure,
  'public.hr_task_rows_mine(uuid,text,integer,uuid)'::regprocedure,
  'public.hr_person_processes(uuid,uuid)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;
