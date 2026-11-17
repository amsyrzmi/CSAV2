-- HR processes, part 3: starting, re-dating and ending processes, from person changes and by hand.
-- Adds (csa_app still has no privilege on the 0630 tables; everything goes through these functions):
--   The trigger hr_process_on_employee AFTER INSERT OR UPDATE OF is_active, last_day, start_date,
--     probation_end, manager_id ON public.hr_employees (FOR EACH ROW, function
--     public.hr_process_on_employee()). It runs inside the writer's transaction (hr_people_apply,
--     hr_finish_leaver, hr_probation_set, fixtures), so a failure rolls the person write back.
--     Tenancy comes from NEW.client_id, never from session settings; the actor of its events is
--     app.hr_user_id, NULL when unset or empty (the leaver worker, direct writes: "System"). In order:
--     1. last_day set to NULL (person.leave_undo, person.rehire): the active offboarding is cancelled.
--     2. insert as active, or is_active false to true (person.add, person.rehire): on a rehire the
--        old employment's active onboarding is cancelled first (reason rehired), whether or not a
--        template matches, so it is never re-dated as the new one; then an onboarding starts when a
--        template matches (hr_process_match), else nothing.
--     3. start_date, last_day or probation_end changed: every active process of the person gets the
--        new anchor_dates, and its open tasks whose due_overridden is false get due_on recomputed.
--     4. last_day NULL to a date while active (person.leave): an offboarding starts when a template
--        matches.
--     5. manager_id changed: the person's open manager tasks are resolved again.
--     6. is_active true to false (hr_finish_leaver): hr_process_person_left.
--   public.hr_process_start(p_client,p_employee,p_kind,p_template,p_actor): the template p_template,
--     or the matching one (not for custom: hr_process_template_required); NULL when none matches.
--     The template must be an active template of the kind (hr_template_unavailable). Ruling R1: an
--     active process of the same person and kind (not custom) is first cancelled (reason
--     superseded); a concurrent start that still meets the one-active index is
--     hr_process_already_active. Copies the steps into hr_process_tasks (the frozen copy, the
--     reference_note included) with the anchor dates {start_date, last_day, probation_end} of the
--     person, due_on = anchor date + due_offset_days (NULL while the anchor date is NULL) and the
--     assignee: manager -> the person's manager when active and linked in hr_employee_accounts, else
--     NULL (the HR pool) with event detail {fallback: 'hr_pool', reason: no_manager | manager_inactive
--     | manager_unlinked}; employee -> the person; person -> owner_employee_id; hr -> NULL (the pool).
--     Spec 5.7.7: in an offboarding, a task of the leaver (employee or person role assigned to the
--     person) due after last_day is due on last_day, event detail {clamped: true}. Events
--     process.started {kind, template_id, template_version} and one task.created {owner_role,
--     assignee_employee_id, due_on} per task.
--   public.hr_process_settle(p_client,p_process): spec 5.7.4, also called by the task actions
--     (0634). An active process whose required tasks are all done or skipped is completed: its open
--     optional tasks are skipped (task.skipped {reason: process_completed}) and process.completed is
--     written, both by the system (actor NULL). Returns whether it completed the process.
--   public.hr_process_person_left(p_client,p_employee,p_actor): the leaver's open (todo or
--     rejected) employee and person tasks in any active process are skipped with the note
--     "person left" (task.skipped {reason: person_left}); open manager tasks still assigned to the
--     leaver are resolved again; open hr tasks HR assigned by hand to the leaver go back to the pool
--     (task.reassigned {from, to: null, fallback: hr_pool, reason: person_left}); open
--     access_removed tasks of the leaver's active offboarding are
--     done by the system (task.completed {auto_check: access_removed}); then every touched process
--     and the offboarding are settled.
--   Internal helpers: hr_process_cancel (open tasks skipped with task.skipped {reason:
--     process_cancelled}, then process.cancelled {reason}), hr_process_redate, hr_process_reassign_manager
--     (task.reassigned {from, to} plus the fallback detail), hr_process_manager_of, hr_process_task_due.
--     Every system change of a task raises its version.
--   public.hr_process_start_apply(p_client,p_key,p_fingerprint,p_body {employee_id, kind,
--     template_id}): HR starts a custom process (template_id required) or an onboarding or
--     offboarding for an existing person (template_id optional: the match). Refusals (ERRCODE 23514,
--     nothing written): hr_process_kind_invalid, hr_employee_unavailable, hr_person_not_active
--     (onboarding and offboarding need an active person), hr_person_not_leaving (offboarding needs a
--     last day), hr_process_template_required, hr_template_unavailable, hr_process_no_template,
--     hr_process_already_active. Result {operation: process.start, process_id, employee_id, kind,
--     template_id, status, duplicate}.
--   public.hr_process_cancel_apply(p_client,p_key,p_fingerprint,p_body {process_id}): HR cancels an
--     active process (reason cancelled_by_hr). A process has no version: its status is the guard
--     (hr_process_closed once it is completed or cancelled); hr_process_unavailable for an id that
--     is not a process of this tenant. Result {operation: process.cancel, process_id, status,
--     duplicate}.
--   public.hr_probation_set(p_client,p_key,p_fingerprint,p_body {employee_id, expected_version,
--     probation_end | null}): sets hr_employees.probation_end and raises version (the trigger
--     re-dates the tasks anchored on it). Refusals: hr_employee_unavailable, hr_person_changed,
--     hr_probation_end_invalid (the key must be present, a date string or null). Result
--     {operation: person.probation_set, resource_id, version, status, duplicate}.
--   The three _apply/_set functions: HR owner or people.manage holder (hr_people_manager_required,
--     ERRCODE 42501, also for another tenant, checked before anything is read), the tenant's
--     hr-operations lock, and the receipt in hr_operations for (key, fingerprint) as
--     hr_template_apply (0631). EXECUTE to csa_app only; every other function here has no grant.
-- No table, column or constraint change. Safe to run twice (CREATE OR REPLACE, trigger dropped and
--   made again).
-- Pre-check: 0630 and 0631 applied; 0521 (hr_finish_leaver, the leaving columns).
-- Rollback (as the database owner, after the application no longer calls them):
--   DROP TRIGGER hr_process_on_employee ON public.hr_employees;
--   DROP FUNCTION public.hr_process_on_employee(); DROP FUNCTION public.hr_probation_set(uuid,uuid,text,jsonb);
--   DROP FUNCTION public.hr_process_cancel_apply(uuid,uuid,text,jsonb);
--   DROP FUNCTION public.hr_process_start_apply(uuid,uuid,text,jsonb);
--   DROP FUNCTION public.hr_process_person_left(uuid,uuid,uuid);
--   DROP FUNCTION public.hr_process_reassign_manager(uuid,uuid,uuid);
--   DROP FUNCTION public.hr_process_redate(uuid,uuid,uuid); DROP FUNCTION public.hr_process_settle(uuid,uuid);
--   DROP FUNCTION public.hr_process_start(uuid,uuid,text,uuid,uuid);
--   DROP FUNCTION public.hr_process_cancel(uuid,uuid,uuid,text);
--   DROP FUNCTION public.hr_process_task_due(text,boolean,text,integer,jsonb);
--   DROP FUNCTION public.hr_process_manager_of(uuid,uuid);
--   then delete the schema_migrations row for 0632. Processes, tasks, events and probation dates
--   already written stay (0630 tables).

-- Who a manager task goes to: the person's manager when active and linked to a console account,
-- else nobody (the HR pool) and why.
CREATE OR REPLACE FUNCTION public.hr_process_manager_of(p_client uuid, p_employee uuid,
    OUT assignee uuid, OUT reason text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE boss uuid; boss_active boolean;
BEGIN
 SELECT e.manager_id INTO boss FROM public.hr_employees e WHERE e.client_id=p_client AND e.id=p_employee;
 IF boss IS NULL THEN reason:='no_manager'; RETURN; END IF;
 SELECT m.is_active INTO boss_active FROM public.hr_employees m WHERE m.client_id=p_client AND m.id=boss;
 IF boss_active IS NOT TRUE THEN reason:='manager_inactive'; RETURN; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.hr_employee_accounts a WHERE a.client_id=p_client AND a.employee_id=boss) THEN
  reason:='manager_unlinked'; RETURN; END IF;
 assignee:=boss;
END $$;

-- A task's due date from the process's anchor dates; spec 5.7.7 clamps a leaver's own offboarding
-- task to last_day.
CREATE OR REPLACE FUNCTION public.hr_process_task_due(p_kind text, p_leaver_owned boolean, p_anchor text,
    p_offset integer, p_dates jsonb, OUT due_on date, OUT clamped boolean)
LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE final_day date:=(p_dates->>'last_day')::date;
BEGIN
 due_on:=(p_dates->>p_anchor)::date+p_offset;
 clamped:=coalesce(p_kind='offboarding' AND p_leaver_owned AND due_on>final_day,false);
 IF clamped THEN due_on:=final_day; END IF;
END $$;

-- Cancels an active process: open tasks skipped, then the process. The caller holds its row lock.
CREATE OR REPLACE FUNCTION public.hr_process_cancel(p_client uuid, p_process uuid, p_actor uuid, p_reason text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 WITH skipped AS (
  UPDATE public.hr_process_tasks k SET status='skipped', version=k.version+1
  WHERE k.client_id=p_client AND k.process_id=p_process AND k.status NOT IN ('done','skipped')
  RETURNING k.id, k.ordinal)
 INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
 SELECT p_client,p_process,s.id,p_actor,'task.skipped',jsonb_build_object('reason','process_cancelled')
 FROM skipped s ORDER BY s.ordinal;
 UPDATE public.hr_processes p SET status='cancelled', closed_at=clock_timestamp()
 WHERE p.client_id=p_client AND p.id=p_process AND p.status='active';
 INSERT INTO public.hr_process_events(client_id,process_id,actor,action,detail)
 VALUES(p_client,p_process,p_actor,'process.cancelled',jsonb_build_object('reason',p_reason));
END $$;

-- Spec 5.7.4: completes an active process whose required tasks are all done or skipped.
CREATE OR REPLACE FUNCTION public.hr_process_settle(p_client uuid, p_process uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE st text;
BEGIN
 SELECT p.status INTO st FROM public.hr_processes p
  WHERE p.client_id=p_client AND p.id=p_process FOR UPDATE;
 IF st IS DISTINCT FROM 'active' THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.hr_process_tasks k
           WHERE k.client_id=p_client AND k.process_id=p_process AND k.required
            AND k.status NOT IN ('done','skipped')) THEN
  RETURN false; END IF;
 WITH skipped AS (
  UPDATE public.hr_process_tasks k SET status='skipped', version=k.version+1
  WHERE k.client_id=p_client AND k.process_id=p_process AND NOT k.required
   AND k.status NOT IN ('done','skipped')
  RETURNING k.id, k.ordinal)
 INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
 SELECT p_client,p_process,s.id,NULL,'task.skipped',jsonb_build_object('reason','process_completed')
 FROM skipped s ORDER BY s.ordinal;
 UPDATE public.hr_processes p SET status='completed', closed_at=clock_timestamp()
 WHERE p.client_id=p_client AND p.id=p_process;
 INSERT INTO public.hr_process_events(client_id,process_id,actor,action,detail)
 VALUES(p_client,p_process,NULL,'process.completed','{}'::jsonb);
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION public.hr_process_start(p_client uuid, p_employee uuid, p_kind text,
    p_template uuid, p_actor uuid)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 tmpl uuid:=p_template; tv integer; dates jsonb; pid uuid; prior uuid; s record;
 who uuid; why text; due date; clamped boolean; tid uuid;
BEGIN
 SELECT jsonb_build_object('start_date',e.start_date,'last_day',e.last_day,'probation_end',e.probation_end)
 INTO dates FROM public.hr_employees e WHERE e.client_id=p_client AND e.id=p_employee;
 IF NOT FOUND THEN RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514'; END IF;
 IF tmpl IS NULL THEN
  -- Custom templates are picked by hand; hr_process_match does not know that.
  IF p_kind='custom' THEN RAISE EXCEPTION 'hr_process_template_required' USING ERRCODE='23514'; END IF;
  tmpl:=public.hr_process_match(p_client,p_employee,p_kind);
  IF tmpl IS NULL THEN RETURN NULL; END IF;
 END IF;
 -- Shared, so a template edit cannot replace the steps between the version and the copy.
 SELECT t.version INTO tv FROM public.hr_process_templates t
  WHERE t.client_id=p_client AND t.id=tmpl AND t.kind=p_kind AND t.is_active FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'hr_template_unavailable' USING ERRCODE='23514'; END IF;
 -- Ruling R1: the one-active index never fails inside a person write.
 IF p_kind<>'custom' THEN
  FOR prior IN SELECT p.id FROM public.hr_processes p
             WHERE p.client_id=p_client AND p.employee_id=p_employee AND p.kind=p_kind
              AND p.status='active'
             ORDER BY p.started_at, p.id FOR UPDATE LOOP
   PERFORM public.hr_process_cancel(p_client,prior,p_actor,'superseded');
  END LOOP;
 END IF;
 BEGIN
  INSERT INTO public.hr_processes(client_id,employee_id,kind,template_id,template_version,anchor_dates,
   started_by)
  VALUES(p_client,p_employee,p_kind,tmpl,tv,dates,p_actor) RETURNING id INTO pid;
 EXCEPTION WHEN unique_violation THEN
  -- Another transaction started one at the same moment (ledger M7).
  RAISE EXCEPTION 'hr_process_already_active' USING ERRCODE='23514';
 END;
 INSERT INTO public.hr_process_events(client_id,process_id,actor,action,detail)
 VALUES(p_client,pid,p_actor,'process.started',
  jsonb_build_object('kind',p_kind,'template_id',tmpl,'template_version',tv));
 FOR s IN SELECT * FROM public.hr_process_template_steps x
          WHERE x.client_id=p_client AND x.template_id=tmpl ORDER BY x.ordinal LOOP
  why:=NULL;
  IF s.owner_role='manager' THEN
   SELECT m.assignee, m.reason INTO who, why FROM public.hr_process_manager_of(p_client,p_employee) m;
  ELSIF s.owner_role='employee' THEN who:=p_employee;
  ELSIF s.owner_role='person' THEN who:=s.owner_employee_id;
  ELSE who:=NULL;
  END IF;
  SELECT d.due_on, d.clamped INTO due, clamped
  FROM public.hr_process_task_due(p_kind, s.owner_role IN ('employee','person') AND who=p_employee,
   s.anchor, s.due_offset_days, dates) d;
  INSERT INTO public.hr_process_tasks(client_id,process_id,ordinal,title_en,title_ms,description_en,
   description_ms,owner_role,assignee_employee_id,anchor,due_offset_days,due_on,required,approval_by,
   auto_check,reference_note)
  VALUES(p_client,pid,s.ordinal,s.title_en,s.title_ms,s.description_en,s.description_ms,s.owner_role,
   who,s.anchor,s.due_offset_days,due,s.required,s.approval_by,s.auto_check,s.reference_note)
  RETURNING id INTO tid;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,pid,tid,p_actor,'task.created',
   jsonb_build_object('owner_role',s.owner_role,'assignee_employee_id',who,'due_on',due)
   || CASE WHEN why IS NOT NULL THEN jsonb_build_object('fallback','hr_pool','reason',why)
           ELSE '{}'::jsonb END
   || CASE WHEN clamped THEN jsonb_build_object('clamped',true) ELSE '{}'::jsonb END);
 END LOOP;
 RETURN pid;
END $$;

-- Spec 5.7.6: new anchor dates for the person's active processes, and new due dates for their open
-- tasks that HR has not moved by hand.
CREATE OR REPLACE FUNCTION public.hr_process_redate(p_client uuid, p_employee uuid, p_actor uuid)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE dates jsonb; p record; t record; due date; clamped boolean;
BEGIN
 SELECT jsonb_build_object('start_date',e.start_date,'last_day',e.last_day,'probation_end',e.probation_end)
 INTO dates FROM public.hr_employees e WHERE e.client_id=p_client AND e.id=p_employee;
 FOR p IN SELECT x.id, x.kind FROM public.hr_processes x
          WHERE x.client_id=p_client AND x.employee_id=p_employee AND x.status='active'
          ORDER BY x.started_at, x.id FOR UPDATE LOOP
  UPDATE public.hr_processes x SET anchor_dates=dates
  WHERE x.client_id=p_client AND x.id=p.id AND x.anchor_dates IS DISTINCT FROM dates;
  FOR t IN SELECT k.id, k.owner_role, k.assignee_employee_id, k.anchor, k.due_offset_days, k.due_on
           FROM public.hr_process_tasks k
           WHERE k.client_id=p_client AND k.process_id=p.id AND NOT k.due_overridden
            AND k.status NOT IN ('done','skipped')
           ORDER BY k.ordinal FOR UPDATE LOOP
   SELECT d.due_on, d.clamped INTO due, clamped
   FROM public.hr_process_task_due(p.kind,
    t.owner_role IN ('employee','person') AND t.assignee_employee_id=p_employee,
    t.anchor, t.due_offset_days, dates) d;
   CONTINUE WHEN due IS NOT DISTINCT FROM t.due_on;
   UPDATE public.hr_process_tasks k SET due_on=due, version=k.version+1
   WHERE k.client_id=p_client AND k.id=t.id;
   INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
   VALUES(p_client,p.id,t.id,p_actor,'task.date_changed',
    jsonb_build_object('anchor',t.anchor,'old',t.due_on,'new',due)
    || CASE WHEN clamped THEN jsonb_build_object('clamped',true) ELSE '{}'::jsonb END);
  END LOOP;
 END LOOP;
END $$;

-- The person's open manager tasks follow the person's (resolvable) manager.
CREATE OR REPLACE FUNCTION public.hr_process_reassign_manager(p_client uuid, p_employee uuid, p_actor uuid)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE who uuid; why text; t record;
BEGIN
 SELECT m.assignee, m.reason INTO who, why FROM public.hr_process_manager_of(p_client,p_employee) m;
 FOR t IN SELECT k.id, k.process_id, k.assignee_employee_id
          FROM public.hr_process_tasks k
          JOIN public.hr_processes x ON x.client_id=k.client_id AND x.id=k.process_id
          WHERE x.client_id=p_client AND x.employee_id=p_employee AND x.status='active'
           AND k.owner_role='manager' AND k.status NOT IN ('done','skipped')
           AND k.assignee_employee_id IS DISTINCT FROM who
          ORDER BY x.started_at, x.id, k.ordinal FOR UPDATE OF k LOOP
  UPDATE public.hr_process_tasks k SET assignee_employee_id=who, version=k.version+1
  WHERE k.client_id=p_client AND k.id=t.id;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,t.process_id,t.id,p_actor,'task.reassigned',
   jsonb_build_object('from',t.assignee_employee_id,'to',who)
   || CASE WHEN why IS NOT NULL THEN jsonb_build_object('fallback','hr_pool','reason',why)
           ELSE '{}'::jsonb END);
 END LOOP;
END $$;

-- Spec 5.2: the leaver's open tasks are skipped, access_removed is checked, and the offboarding (and
-- any process the skips finished) completes when nothing required is left.
CREATE OR REPLACE FUNCTION public.hr_process_person_left(p_client uuid, p_employee uuid, p_actor uuid)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE touched uuid[]:='{}'; t record; other uuid; pid uuid;
BEGIN
 -- A task waiting for approval was done by the leaver: it stays for the approver.
 FOR t IN SELECT k.id, k.process_id FROM public.hr_process_tasks k
          JOIN public.hr_processes x ON x.client_id=k.client_id AND x.id=k.process_id
          WHERE k.client_id=p_client AND x.status='active' AND k.assignee_employee_id=p_employee
           AND k.owner_role IN ('employee','person') AND k.status IN ('todo','rejected')
          ORDER BY x.started_at, x.id, k.ordinal FOR UPDATE OF k LOOP
  UPDATE public.hr_process_tasks k SET status='skipped', note=concat_ws(E'\n',k.note,'person left'),
   version=k.version+1
  WHERE k.client_id=p_client AND k.id=t.id;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,t.process_id,t.id,p_actor,'task.skipped',jsonb_build_object('reason','person_left'));
  touched:=touched||t.process_id;
 END LOOP;
 -- Manager tasks of people whose manager is still the leaver (reports not moved) go to the pool.
 FOR other IN SELECT DISTINCT x.employee_id FROM public.hr_process_tasks k
              JOIN public.hr_processes x ON x.client_id=k.client_id AND x.id=k.process_id
              WHERE k.client_id=p_client AND x.status='active' AND k.owner_role='manager'
               AND k.assignee_employee_id=p_employee AND k.status NOT IN ('done','skipped') LOOP
  PERFORM public.hr_process_reassign_manager(p_client,other,p_actor);
 END LOOP;
 -- HR tasks HR handed to the leaver by hand go back to the HR pool.
 FOR t IN SELECT k.id, k.process_id FROM public.hr_process_tasks k
          JOIN public.hr_processes x ON x.client_id=k.client_id AND x.id=k.process_id
          WHERE k.client_id=p_client AND x.status='active' AND k.owner_role='hr'
           AND k.assignee_employee_id=p_employee AND k.status NOT IN ('done','skipped')
          ORDER BY x.started_at, x.id, k.ordinal FOR UPDATE OF k LOOP
  UPDATE public.hr_process_tasks k SET assignee_employee_id=NULL, version=k.version+1
  WHERE k.client_id=p_client AND k.id=t.id;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,t.process_id,t.id,p_actor,'task.reassigned',
   jsonb_build_object('from',p_employee,'to',NULL,'fallback','hr_pool','reason','person_left'));
 END LOOP;
 FOR t IN SELECT k.id, k.process_id FROM public.hr_process_tasks k
          JOIN public.hr_processes x ON x.client_id=k.client_id AND x.id=k.process_id
          WHERE k.client_id=p_client AND x.employee_id=p_employee AND x.kind='offboarding'
           AND x.status='active' AND k.auto_check='access_removed' AND k.status NOT IN ('done','skipped')
          ORDER BY k.ordinal FOR UPDATE OF k LOOP
  UPDATE public.hr_process_tasks k SET status='done', completed_at=clock_timestamp(), version=k.version+1
  WHERE k.client_id=p_client AND k.id=t.id;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,t.process_id,t.id,p_actor,'task.completed',
   jsonb_build_object('auto_check','access_removed'));
  touched:=touched||t.process_id;
 END LOOP;
 FOR pid IN SELECT DISTINCT u.id FROM unnest(touched||ARRAY(
              SELECT x.id FROM public.hr_processes x
              WHERE x.client_id=p_client AND x.employee_id=p_employee AND x.kind='offboarding'
               AND x.status='active')) u(id) LOOP
  PERFORM public.hr_process_settle(p_client,pid);
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION public.hr_process_on_employee() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 was_active boolean:=false; old_last date; old_start date; old_probation date;
 pid uuid;
BEGIN
 IF TG_OP='UPDATE' THEN
  was_active:=OLD.is_active; old_last:=OLD.last_day; old_start:=OLD.start_date;
  old_probation:=OLD.probation_end;
 END IF;
 -- 1. The leaving plan is withdrawn (undo, rehire).
 IF old_last IS NOT NULL AND NEW.last_day IS NULL THEN
  FOR pid IN SELECT p.id FROM public.hr_processes p
             WHERE p.client_id=NEW.client_id AND p.employee_id=NEW.id AND p.kind='offboarding'
              AND p.status='active'
             ORDER BY p.started_at, p.id FOR UPDATE LOOP
   PERFORM public.hr_process_cancel(NEW.client_id,pid,actor,'last_day_cleared');
  END LOOP;
 END IF;
 -- 2. A new or returning person. A rehire first closes the old employment's onboarding, whether
 -- or not a template matches now, so step 3 never re-dates it as the new one.
 IF NEW.is_active AND was_active IS NOT TRUE THEN
  IF TG_OP='UPDATE' THEN
   FOR pid IN SELECT p.id FROM public.hr_processes p
              WHERE p.client_id=NEW.client_id AND p.employee_id=NEW.id AND p.kind='onboarding'
               AND p.status='active'
              ORDER BY p.started_at, p.id FOR UPDATE LOOP
    PERFORM public.hr_process_cancel(NEW.client_id,pid,actor,'rehired');
   END LOOP;
  END IF;
  PERFORM public.hr_process_start(NEW.client_id,NEW.id,'onboarding',NULL,actor);
 END IF;
 -- 3. Anchor dates moved.
 IF TG_OP='UPDATE' AND (old_start IS DISTINCT FROM NEW.start_date OR old_last IS DISTINCT FROM NEW.last_day
                        OR old_probation IS DISTINCT FROM NEW.probation_end) THEN
  PERFORM public.hr_process_redate(NEW.client_id,NEW.id,actor);
 END IF;
 -- 4. Marked as leaving.
 IF NEW.is_active AND old_last IS NULL AND NEW.last_day IS NOT NULL THEN
  PERFORM public.hr_process_start(NEW.client_id,NEW.id,'offboarding',NULL,actor);
 END IF;
 -- 5. A new manager.
 IF TG_OP='UPDATE' AND OLD.manager_id IS DISTINCT FROM NEW.manager_id THEN
  PERFORM public.hr_process_reassign_manager(NEW.client_id,NEW.id,actor);
 END IF;
 -- 6. Left.
 IF was_active AND NEW.is_active IS NOT TRUE THEN
  PERFORM public.hr_process_person_left(NEW.client_id,NEW.id,actor);
 END IF;
 RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS hr_process_on_employee ON public.hr_employees;
CREATE TRIGGER hr_process_on_employee
 AFTER INSERT OR UPDATE OF is_active, last_day, start_date, probation_end, manager_id
 ON public.hr_employees FOR EACH ROW EXECUTE FUNCTION public.hr_process_on_employee();

CREATE OR REPLACE FUNCTION public.hr_process_start_apply(p_client uuid, p_key uuid, p_fingerprint text,
    p_body jsonb)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 old_op public.hr_operations%ROWTYPE; e public.hr_employees%ROWTYPE;
 pkind text:=p_body->>'kind'; target uuid; tmpl uuid; pid uuid; result jsonb;
BEGIN
 -- Authority first: hr_process_match below does not check the tenant. Both checks are false for
 -- another tenant than app.client_id.
 IF public.hr_is_owner(p_client) IS NOT TRUE AND public.hr_can_manage_people(p_client) IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_people_manager_required' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-operations',0));
 SELECT * INTO old_op FROM public.hr_operations o
  WHERE o.client_id=p_client AND o.subject_id=actor AND o.request_key=p_key;
 IF FOUND THEN
  IF old_op.fingerprint IS DISTINCT FROM p_fingerprint THEN
   RAISE EXCEPTION 'hr_idempotency_conflict' USING ERRCODE='23514'; END IF;
  RETURN old_op.receipt||jsonb_build_object('duplicate',true);
 END IF;
 IF pkind IS NULL OR pkind NOT IN ('onboarding','offboarding','custom') THEN
  RAISE EXCEPTION 'hr_process_kind_invalid' USING ERRCODE='23514'; END IF;
 BEGIN
  target:=(p_body->>'employee_id')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514';
 END;
 BEGIN
  tmpl:=(p_body->>'template_id')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'hr_template_unavailable' USING ERRCODE='23514';
 END;
 -- NO KEY UPDATE still waits for the person writes (they take FOR UPDATE or update the row), but
 -- not for a concurrent new process, which takes only KEY SHARE for its foreign key: that one
 -- meets the one-active index (hr_process_already_active), never a silent supersede.
 SELECT * INTO e FROM public.hr_employees x WHERE x.client_id=p_client AND x.id=target FOR NO KEY UPDATE;
 IF target IS NULL OR NOT FOUND THEN RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514'; END IF;
 IF pkind<>'custom' AND e.is_active IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_person_not_active' USING ERRCODE='23514'; END IF;
 IF pkind='offboarding' AND e.last_day IS NULL THEN
  RAISE EXCEPTION 'hr_person_not_leaving' USING ERRCODE='23514'; END IF;
 pid:=public.hr_process_start(p_client,target,pkind,tmpl,actor);
 IF pid IS NULL THEN RAISE EXCEPTION 'hr_process_no_template' USING ERRCODE='23514'; END IF;
 SELECT jsonb_build_object('operation','process.start','process_id',p.id,'employee_id',p.employee_id,
  'kind',p.kind,'template_id',p.template_id,'status','saved','duplicate',false)
 INTO result FROM public.hr_processes p WHERE p.client_id=p_client AND p.id=pid;
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
 VALUES(p_client,actor,p_key,'process.start',p_fingerprint,result);
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION public.hr_process_cancel_apply(p_client uuid, p_key uuid, p_fingerprint text,
    p_body jsonb)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 old_op public.hr_operations%ROWTYPE; target uuid; st text; result jsonb;
BEGIN
 IF public.hr_is_owner(p_client) IS NOT TRUE AND public.hr_can_manage_people(p_client) IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_people_manager_required' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-operations',0));
 SELECT * INTO old_op FROM public.hr_operations o
  WHERE o.client_id=p_client AND o.subject_id=actor AND o.request_key=p_key;
 IF FOUND THEN
  IF old_op.fingerprint IS DISTINCT FROM p_fingerprint THEN
   RAISE EXCEPTION 'hr_idempotency_conflict' USING ERRCODE='23514'; END IF;
  RETURN old_op.receipt||jsonb_build_object('duplicate',true);
 END IF;
 BEGIN
  target:=(p_body->>'process_id')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'hr_process_unavailable' USING ERRCODE='23514';
 END;
 SELECT p.status INTO st FROM public.hr_processes p WHERE p.client_id=p_client AND p.id=target FOR UPDATE;
 IF target IS NULL OR NOT FOUND THEN RAISE EXCEPTION 'hr_process_unavailable' USING ERRCODE='23514'; END IF;
 IF st<>'active' THEN RAISE EXCEPTION 'hr_process_closed' USING ERRCODE='23514'; END IF;
 PERFORM public.hr_process_cancel(p_client,target,actor,'cancelled_by_hr');
 result:=jsonb_build_object('operation','process.cancel','process_id',target,'status','saved',
  'duplicate',false);
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
 VALUES(p_client,actor,p_key,'process.cancel',p_fingerprint,result);
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION public.hr_probation_set(p_client uuid, p_key uuid, p_fingerprint text,
    p_body jsonb)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 old_op public.hr_operations%ROWTYPE; cur public.hr_employees%ROWTYPE;
 target uuid; probation date; v integer; result jsonb;
BEGIN
 IF public.hr_is_owner(p_client) IS NOT TRUE AND public.hr_can_manage_people(p_client) IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_people_manager_required' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-operations',0));
 SELECT * INTO old_op FROM public.hr_operations o
  WHERE o.client_id=p_client AND o.subject_id=actor AND o.request_key=p_key;
 IF FOUND THEN
  IF old_op.fingerprint IS DISTINCT FROM p_fingerprint THEN
   RAISE EXCEPTION 'hr_idempotency_conflict' USING ERRCODE='23514'; END IF;
  RETURN old_op.receipt||jsonb_build_object('duplicate',true);
 END IF;
 BEGIN
  target:=(p_body->>'employee_id')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514';
 END;
 SELECT * INTO cur FROM public.hr_employees e WHERE e.client_id=p_client AND e.id=target FOR UPDATE;
 IF target IS NULL OR NOT FOUND THEN RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514'; END IF;
 IF jsonb_typeof(p_body->'expected_version') IS DISTINCT FROM 'number'
  OR cur.version::numeric<>(p_body->>'expected_version')::numeric THEN
  RAISE EXCEPTION 'hr_person_changed' USING ERRCODE='23514'; END IF;
 IF jsonb_typeof(p_body->'probation_end') IS NULL
  OR jsonb_typeof(p_body->'probation_end') NOT IN ('string','null') THEN
  RAISE EXCEPTION 'hr_probation_end_invalid' USING ERRCODE='23514'; END IF;
 BEGIN
  probation:=(p_body->>'probation_end')::date;
 EXCEPTION WHEN data_exception THEN
  RAISE EXCEPTION 'hr_probation_end_invalid' USING ERRCODE='23514';
 END;
 UPDATE public.hr_employees e SET probation_end=probation, version=e.version+1
 WHERE e.client_id=p_client AND e.id=target RETURNING e.version INTO v;
 result:=jsonb_build_object('operation','person.probation_set','resource_id',target,'version',v,
  'status','saved','duplicate',false);
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
 VALUES(p_client,actor,p_key,'person.probation_set',p_fingerprint,result);
 RETURN result;
END $$;

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_process_manager_of(uuid,uuid)'::regprocedure,
  'public.hr_process_task_due(text,boolean,text,integer,jsonb)'::regprocedure,
  'public.hr_process_cancel(uuid,uuid,uuid,text)'::regprocedure,
  'public.hr_process_settle(uuid,uuid)'::regprocedure,
  'public.hr_process_start(uuid,uuid,text,uuid,uuid)'::regprocedure,
  'public.hr_process_redate(uuid,uuid,uuid)'::regprocedure,
  'public.hr_process_reassign_manager(uuid,uuid,uuid)'::regprocedure,
  'public.hr_process_person_left(uuid,uuid,uuid)'::regprocedure,
  'public.hr_process_on_employee()'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance',f);
 END LOOP;
 FOREACH f IN ARRAY ARRAY['public.hr_process_start_apply(uuid,uuid,text,jsonb)'::regprocedure,
  'public.hr_process_cancel_apply(uuid,uuid,text,jsonb)'::regprocedure,
  'public.hr_probation_set(uuid,uuid,text,jsonb)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;
