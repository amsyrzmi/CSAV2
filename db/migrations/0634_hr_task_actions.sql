-- HR processes, part 5: task actions and approvals, and who gets a leaver's open steps.
-- Adds (csa_app still has no privilege on the 0630 tables; everything goes through these functions):
--   public.hr_process_tasks.assignee_overridden boolean NOT NULL DEFAULT false: true once HR has
--     reassigned the task by hand (task.reassign). A manager change (0632 trigger step 5, through
--     hr_process_reassign_manager) no longer moves such a task.
--   public.hr_process_tasks.sent_back_reason text NULL (at most 500 characters): why the approver
--     sent the step back, addressed to the assignee; set by task.send_back, cleared by the next
--     task.complete. Shown to the assignee and the HR team (My tasks, and the own and HR task
--     shapes of hr_process_get), never in a manager's view of other people's tasks.
--   public.hr_task_apply(p_client,p_key,p_fingerprint,p_body): every task action. Who is asking
--     comes from hr_process_caller (0633): the HR team (HR owner or people.manage holder) or the
--     caller's active linked employee; anyone else, and anyone for another tenant than
--     app.client_id, gets hr_task_unavailable before anything is read. Then the tenant's
--     hr-operations lock FIRST (the process and task row locks below are taken in varying order
--     by 0632, which is safe only under that lock), the receipt in hr_operations for (key,
--     fingerprint) as hr_template_apply (0631), the process row and then the task row FOR UPDATE.
--     A task id of another tenant, or of a task the caller has no part in (not HR, not its
--     assignee, not the manager of the process's person), is hr_task_unavailable. A task that is
--     done or skipped, or whose process is no longer active, is hr_task_closed. Every action but
--     task.note and process.close_anyway needs expected_version (hr_task_changed when stale).
--     Every action raises the task's version, writes one hr_process_events row with the real
--     actor (app.hr_user_id) and calls hr_process_settle (0632). Operations:
--     task.complete {task_id, expected_version, note?}: the assignee or the HR team (a pool task:
--       any HR team member; anyone's task on their behalf). awaiting_approval is
--       hr_task_awaiting_approval; access_removed is hr_task_system_only (the leaver finish
--       completes it). An automatic check (account_linked: the person has a console account
--       link; personal_details_complete: the five contact fields the person fills in themself,
--       home_address, personal_phone, emergency_name, emergency_relationship and
--       emergency_phone, are all set; document_uploaded:<kind>: a document of that kind of the
--       person is on file, not removed) must hold, else hr_task_check_not_met (HR may skip). A
--       step with approval_by goes to awaiting_approval, else done; completed_by and
--       completed_at are set, sent_back_reason is cleared; a note given replaces the shared note.
--       Event task.completed
--       {awaiting_approval, on_behalf (a named assignee who is not the caller), auto_check}.
--     task.approve {task_id, expected_version}: the task must be awaiting_approval
--       (hr_task_not_awaiting_approval). The approver (hr_task_approver): approval_by hr is the
--       HR team; approval_by manager is the person's manager when active and linked (0632
--       hr_process_manager_of), and falls back to the HR team when no manager resolves, when the
--       step is the manager's own (owner_role manager: the manager of the same person can never
--       approve it) or when the manager completed it. Anyone else: hr_task_not_approver. The
--       person who completed it cannot approve (hr_task_self_approval), except the HR owner when
--       no other HR team member (enabled membership, not read-only) exists (spec 5.7.2). Done,
--       approved_by and approved_at set. Event task.approved {approver: hr | manager, and
--       approved_without_second_person: true for that exception}.
--     task.send_back {task_id, expected_version, reason}: the approver (the completer included).
--       Back to todo, completed_by and completed_at cleared, sent_back_reason set to the reason
--       (the assignee sees it; the note is untouched), event task.sent_back {remark}. When
--       the assignee is a person who has left, the task is skipped instead, never reopened for
--       them (event task.skipped {reason: person_left, remark, sent_back: true}).
--     task.note {task_id, note}: the assignee or the HR team; the shared note (seen by the
--       assignee) is replaced, null or an empty string clears it (at most 2000 characters, else
--       hr_task_note_invalid). Event task.noted {}.
--     task.skip {task_id, expected_version, reason}: the HR team. Event task.skipped {reason:
--       skipped_by_hr, remark}.
--     task.reschedule {task_id, expected_version, due_on}: the HR team; due_on a date
--       (hr_task_due_invalid), due_overridden set, so a later date change leaves it. Event
--       task.rescheduled {old, new}.
--     task.reassign {task_id, expected_version, assignee_employee_id}: the HR team; an active
--       employee of this tenant (hr_task_assignee_unavailable) or null (the HR pool);
--       assignee_overridden set. Event task.reassigned {from, to, reason: reassigned_by_hr}.
--     process.close_anyway {process_id, note}: the HR team (spec 5.7.4). The process must be
--       active (hr_process_unavailable, hr_process_closed); its open tasks are skipped
--       (task.skipped {reason: process_closed_anyway}) and it completes (process.completed
--       {reason: closed_anyway, remark}).
--     HR-only remarks (the reasons of skip and close anyway) are kept in the event detail as
--     remark (events are HR-only), never in the task note, which the assignee sees. A send-back
--     reason is a message to the assignee: it is kept in sent_back_reason, and in the event as
--     remark for the record.
--     A reason or a close-anyway note is 1-500 characters after trimming (hr_task_reason_invalid).
--     Skip, reschedule, reassign and close anyway by anyone outside the HR team:
--     hr_people_manager_required; complete and note by someone else: hr_task_not_assignee; an
--     unknown operation: hr_operation_unsupported. Result {operation, task_id, process_id,
--     task_status, version, process_status, status: saved, duplicate} ({operation, process_id,
--     process_status, status, duplicate} for close anyway). EXECUTE to csa_app only.
--   public.hr_task_rows_mine (0633) is dropped and made again with one more column,
--     needs_my_approval: the tasks awaiting approval the caller may approve now are listed too
--     (the HR team: approver hr, which includes manager approval with no manager resolved; the
--     manager: approver manager of their report), true on those rows. A manager who is neither
--     the assignee nor HR gets neither the note nor the descriptions and reference note of such a
--     row (0633's manager view withholds them), only title, status, due date and the rest. A
--     last column sent_back_reason goes to the assignee and the HR team. Every other column, the
--     visibility of the other rows and the order (soonest due first, keyset by task id) are
--     unchanged.
--   Changed from 0632 (CREATE OR REPLACE, the rest of each body unchanged):
--     hr_process_start: a person step whose named owner is no longer active, and an employee step
--       of a process for a person who is not active (a custom process may be started for someone
--       who has left), go to the HR pool, event task.created detail {fallback: hr_pool, reason:
--       person_inactive}.
--   Changed from 0633 (CREATE OR REPLACE, the rest of the body unchanged): hr_process_get adds
--     sent_back_reason to the own task shape (the caller's own tasks, and every task for HR).
--     hr_process_reassign_manager: skips tasks HR reassigned by hand (assignee_overridden).
--     hr_process_person_left: when a person leaves, (1) their own active onboarding and custom
--       processes are cancelled (process.cancelled {reason: person_left}; open tasks skipped as
--       for any cancel), (2) in their own remaining processes (the offboarding) their open (todo)
--       employee and person tasks are skipped as before, a task awaiting approval stays for its
--       approver, (3) open manager tasks still assigned to them, not reassigned by HR, are
--       resolved again (a moved report's new manager, else the HR pool) as before, (4) every
--       other open task assigned to them goes back to the HR pool (task.reassigned {from, to:
--       null, fallback: hr_pool, reason: person_left}): a colleague's step in someone else's
--       process is real work that HR reassigns or skips, never skipped silently (spec section
--       2), (5) access_removed and the settles as before.
--   Internal helpers, no grant to any application role: hr_task_check (an automatic check),
--     hr_task_approver (who approves a task), hr_task_approval_right (what the caller may do with
--     an awaiting task: manager, hr, hr_completer (may send back, not approve), hr_sole_owner).
-- Pre-check: 0630-0633 applied. ALTER TABLE ADD COLUMN with a constant default is a catalog change
--   (no rewrite) with a brief lock on hr_process_tasks. Safe to run twice (IF NOT EXISTS, CREATE OR
--   REPLACE, DROP FUNCTION IF EXISTS before the new hr_task_rows_mine).
-- Rollback (as the database owner, after the application no longer calls them):
--   DROP FUNCTION public.hr_task_apply(uuid,uuid,text,jsonb);
--   DROP FUNCTION public.hr_task_approval_right(uuid,uuid); DROP FUNCTION public.hr_task_approver(uuid,uuid);
--   DROP FUNCTION public.hr_task_check(uuid,uuid,text);
--   re-run the hr_task_rows_mine part of 0633 after DROP FUNCTION public.hr_task_rows_mine(uuid,text,integer,uuid),
--   the hr_process_get part of 0633, and the hr_process_start, hr_process_reassign_manager and
--   hr_process_person_left parts of 0632; ALTER TABLE public.hr_process_tasks DROP COLUMN
--   assignee_overridden, DROP COLUMN sent_back_reason (who reassigned by hand and open send-back
--   reasons are lost); then delete the schema_migrations row for 0634. Events already written stay.

ALTER TABLE public.hr_process_tasks ADD COLUMN IF NOT EXISTS assignee_overridden boolean NOT NULL DEFAULT false;
ALTER TABLE public.hr_process_tasks ADD COLUMN IF NOT EXISTS sent_back_reason text
 CHECK(char_length(sent_back_reason)<=500);

-- 0632, with ledger I3: a person step whose named owner has left goes to the HR pool, and so does an
-- employee step of a person who is not active (review m2: a custom process for someone who left).
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
  ELSIF s.owner_role='employee' THEN
   who:=p_employee;
   IF NOT EXISTS(SELECT 1 FROM public.hr_employees o
                 WHERE o.client_id=p_client AND o.id=who AND o.is_active) THEN
    who:=NULL; why:='person_inactive';
   END IF;
  ELSIF s.owner_role='person' THEN
   who:=s.owner_employee_id;
   -- The template was saved while the person was active (0631); they may have left since.
   IF NOT EXISTS(SELECT 1 FROM public.hr_employees o
                 WHERE o.client_id=p_client AND o.id=who AND o.is_active) THEN
    who:=NULL; why:='person_inactive';
   END IF;
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

-- 0632, except that a task HR reassigned by hand stays where HR put it.
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
           AND NOT k.assignee_overridden
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

-- Spec 5.2 with ledger I4 and the T3 M7 routing: the leaver's own onboarding and custom processes
-- end, their own open steps in the offboarding are skipped, and their steps in other people's
-- processes go to the HR pool.
CREATE OR REPLACE FUNCTION public.hr_process_person_left(p_client uuid, p_employee uuid, p_actor uuid)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE touched uuid[]:='{}'; t record; other uuid; pid uuid;
BEGIN
 -- 1. Their own onboarding and custom processes are about an employment that has ended.
 FOR pid IN SELECT x.id FROM public.hr_processes x
            WHERE x.client_id=p_client AND x.employee_id=p_employee AND x.status='active'
             AND x.kind IN ('onboarding','custom')
            ORDER BY x.started_at, x.id FOR UPDATE LOOP
  PERFORM public.hr_process_cancel(p_client,pid,p_actor,'person_left');
 END LOOP;
 -- 2. Their own steps in their own (offboarding) process. A task waiting for approval was done by
 -- the leaver: it stays for the approver.
 FOR t IN SELECT k.id, k.process_id FROM public.hr_process_tasks k
          JOIN public.hr_processes x ON x.client_id=k.client_id AND x.id=k.process_id
          WHERE k.client_id=p_client AND x.status='active' AND x.employee_id=p_employee
           AND k.assignee_employee_id=p_employee
           AND k.owner_role IN ('employee','person') AND k.status IN ('todo','rejected')
          ORDER BY x.started_at, x.id, k.ordinal FOR UPDATE OF k LOOP
  UPDATE public.hr_process_tasks k SET status='skipped', note=concat_ws(E'\n',k.note,'person left'),
   version=k.version+1
  WHERE k.client_id=p_client AND k.id=t.id;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,t.process_id,t.id,p_actor,'task.skipped',jsonb_build_object('reason','person_left'));
  touched:=touched||t.process_id;
 END LOOP;
 -- 3. Manager tasks of people whose manager is still the leaver (reports not moved) follow the
 -- manager rule (the HR pool); HR's own choices are left to step 4.
 FOR other IN SELECT DISTINCT x.employee_id FROM public.hr_process_tasks k
              JOIN public.hr_processes x ON x.client_id=k.client_id AND x.id=k.process_id
              WHERE k.client_id=p_client AND x.status='active' AND k.owner_role='manager'
               AND NOT k.assignee_overridden
               AND k.assignee_employee_id=p_employee AND k.status NOT IN ('done','skipped') LOOP
  PERFORM public.hr_process_reassign_manager(p_client,other,p_actor);
 END LOOP;
 -- 4. Everything else still assigned to the leaver goes back to the HR pool: a colleague's step
 -- in someone else's process, and a task HR handed to the leaver by hand.
 FOR t IN SELECT k.id, k.process_id FROM public.hr_process_tasks k
          JOIN public.hr_processes x ON x.client_id=k.client_id AND x.id=k.process_id
          WHERE k.client_id=p_client AND x.status='active'
           AND k.assignee_employee_id=p_employee AND k.status NOT IN ('done','skipped')
           AND NOT (x.employee_id=p_employee AND k.owner_role IN ('employee','person'))
           AND NOT (k.owner_role='manager' AND NOT k.assignee_overridden)
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

-- Whether an automatic check holds for the process's person.
CREATE OR REPLACE FUNCTION public.hr_task_check(p_client uuid, p_employee uuid, p_check text)
RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_check='account_linked' THEN
  RETURN EXISTS(SELECT 1 FROM public.hr_employee_accounts a
                WHERE a.client_id=p_client AND a.employee_id=p_employee);
 ELSIF p_check='personal_details_complete' THEN
  -- The contact fields the person may fill in themself (0521 hr_personal_apply, self).
  RETURN EXISTS(SELECT 1 FROM public.hr_employee_personal x
                WHERE x.client_id=p_client AND x.employee_id=p_employee
                 AND nullif(btrim(x.home_address),'') IS NOT NULL
                 AND nullif(btrim(x.personal_phone),'') IS NOT NULL
                 AND nullif(btrim(x.emergency_name),'') IS NOT NULL
                 AND nullif(btrim(x.emergency_relationship),'') IS NOT NULL
                 AND nullif(btrim(x.emergency_phone),'') IS NOT NULL);
 ELSIF p_check LIKE 'document\_uploaded:%' THEN
  RETURN EXISTS(SELECT 1 FROM public.hr_employee_documents d
                WHERE d.client_id=p_client AND d.employee_id=p_employee AND d.removed_at IS NULL
                 AND d.kind=substr(p_check,length('document_uploaded:')+1));
 END IF;
 RETURN false;
END $$;

-- Who approves a task: approver 'manager' (manager = the person's manager employee) or 'hr' (the
-- HR team); NULL for a task without approval_by. The manager approves only when they resolve
-- (active and linked, 0632 hr_process_manager_of), the step is not the manager's own and they did
-- not complete it themself: a manager step approved by the manager would need the same person
-- twice, so it falls to the HR team.
CREATE OR REPLACE FUNCTION public.hr_task_approver(p_client uuid, p_task uuid,
    OUT approver text, OUT manager uuid)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE k record; boss uuid;
BEGIN
 SELECT t.approval_by, t.owner_role, t.completed_by, x.employee_id INTO k
 FROM public.hr_process_tasks t
 JOIN public.hr_processes x ON x.client_id=t.client_id AND x.id=t.process_id
 WHERE t.client_id=p_client AND t.id=p_task;
 IF NOT FOUND OR k.approval_by IS NULL THEN RETURN; END IF;
 approver:='hr';
 IF k.approval_by='manager' AND k.owner_role<>'manager' THEN
  SELECT m.assignee INTO boss FROM public.hr_process_manager_of(p_client,k.employee_id) m;
  IF boss IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.hr_employee_accounts a
                                     WHERE a.client_id=p_client AND a.employee_id=boss
                                      AND a.user_id=k.completed_by) THEN
   approver:='manager'; manager:=boss;
  END IF;
 END IF;
END $$;

-- What the caller may do with a task awaiting approval: 'manager' or 'hr' (approve or send back),
-- 'hr_completer' (the HR approver who completed it: send back only), 'hr_sole_owner' (the HR
-- owner who completed it with no other HR team member: may approve, spec 5.7.2), NULL (nothing).
CREATE OR REPLACE FUNCTION public.hr_task_approval_right(p_client uuid, p_task uuid) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE me uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid; who record; a record;
 done_by uuid;
BEGIN
 SELECT * INTO who FROM public.hr_process_caller(p_client) c;
 SELECT * INTO a FROM public.hr_task_approver(p_client,p_task) r;
 IF a.approver IS NULL THEN RETURN NULL; END IF;
 IF a.approver='manager' THEN
  RETURN CASE WHEN who.employee=a.manager THEN 'manager' END;
 END IF;
 IF NOT who.team THEN RETURN NULL; END IF;
 SELECT t.completed_by INTO done_by FROM public.hr_process_tasks t
  WHERE t.client_id=p_client AND t.id=p_task;
 IF done_by IS DISTINCT FROM me THEN RETURN 'hr'; END IF;
 IF public.hr_is_owner(p_client) AND NOT EXISTS(
   SELECT 1 FROM public.console_memberships m
   WHERE m.client_id=p_client AND m.user_id<>me AND m.is_enabled AND m.role<>'read_only'
    AND (EXISTS(SELECT 1 FROM public.hr_client_owners o
                WHERE o.client_id=p_client AND o.user_id=m.user_id)
         OR EXISTS(SELECT 1 FROM public.hr_grants g
                   WHERE g.client_id=p_client AND g.user_id=m.user_id
                    AND g.action='people.manage' AND g.scope='hr_operator'))) THEN
  RETURN 'hr_sole_owner';
 END IF;
 RETURN 'hr_completer';
END $$;

CREATE OR REPLACE FUNCTION public.hr_task_apply(p_client uuid, p_key uuid, p_fingerprint text,
    p_body jsonb)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 op text:=p_body->>'operation';
 who record; old_op public.hr_operations%ROWTYPE; k public.hr_process_tasks%ROWTYPE;
 p public.hr_processes%ROWTYPE; target uuid; pid uuid; boss uuid; mine boolean; right_ text;
 txt text; has_note boolean:=false; due date; newly uuid; result jsonb;
BEGIN
 -- Who is asking, before anything is read: nobody for another tenant than app.client_id.
 SELECT * INTO who FROM public.hr_process_caller(p_client) c;
 IF actor IS NULL OR (who.team IS NOT TRUE AND who.employee IS NULL) THEN
  RAISE EXCEPTION 'hr_task_unavailable' USING ERRCODE='23514'; END IF;
 -- First, before any row lock: 0632 locks processes and tasks in varying order (ledger T3 M5).
 PERFORM pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-operations',0));
 SELECT * INTO old_op FROM public.hr_operations o
  WHERE o.client_id=p_client AND o.subject_id=actor AND o.request_key=p_key;
 IF FOUND THEN
  IF old_op.fingerprint IS DISTINCT FROM p_fingerprint THEN
   RAISE EXCEPTION 'hr_idempotency_conflict' USING ERRCODE='23514'; END IF;
  RETURN old_op.receipt||jsonb_build_object('duplicate',true);
 END IF;
 IF op IS NULL OR op NOT IN ('task.complete','task.skip','task.approve','task.send_back','task.note',
                             'task.reschedule','task.reassign','process.close_anyway') THEN
  RAISE EXCEPTION 'hr_operation_unsupported' USING ERRCODE='23514'; END IF;
 -- An HR remark (kept in the event only): the reason of a skip or send back, the close-anyway note.
 IF op IN ('task.skip','task.send_back') THEN
  txt:=CASE WHEN jsonb_typeof(p_body->'reason')='string' THEN btrim(p_body->>'reason') END;
 ELSIF op='process.close_anyway' THEN
  txt:=CASE WHEN jsonb_typeof(p_body->'note')='string' THEN btrim(p_body->>'note') END;
 END IF;

 IF op='process.close_anyway' THEN
  IF who.team IS NOT TRUE THEN RAISE EXCEPTION 'hr_people_manager_required' USING ERRCODE='42501'; END IF;
  BEGIN
   pid:=(p_body->>'process_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
   RAISE EXCEPTION 'hr_process_unavailable' USING ERRCODE='23514';
  END;
  SELECT * INTO p FROM public.hr_processes x WHERE x.client_id=p_client AND x.id=pid FOR UPDATE;
  IF pid IS NULL OR NOT FOUND THEN RAISE EXCEPTION 'hr_process_unavailable' USING ERRCODE='23514'; END IF;
  IF p.status<>'active' THEN RAISE EXCEPTION 'hr_process_closed' USING ERRCODE='23514'; END IF;
  IF txt IS NULL OR char_length(txt) NOT BETWEEN 1 AND 500 THEN
   RAISE EXCEPTION 'hr_task_reason_invalid' USING ERRCODE='23514'; END IF;
  WITH skipped AS (
   UPDATE public.hr_process_tasks t SET status='skipped', version=t.version+1
   WHERE t.client_id=p_client AND t.process_id=pid AND t.status NOT IN ('done','skipped')
   RETURNING t.id, t.ordinal)
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  SELECT p_client,pid,s.id,actor,'task.skipped',jsonb_build_object('reason','process_closed_anyway')
  FROM skipped s ORDER BY s.ordinal;
  UPDATE public.hr_processes x SET status='completed', closed_at=clock_timestamp()
  WHERE x.client_id=p_client AND x.id=pid;
  INSERT INTO public.hr_process_events(client_id,process_id,actor,action,detail)
  VALUES(p_client,pid,actor,'process.completed',
   jsonb_build_object('reason','closed_anyway','remark',txt));
  PERFORM public.hr_process_settle(p_client,pid);
  result:=jsonb_build_object('operation',op,'process_id',pid,'process_status','completed',
   'status','saved','duplicate',false);
  INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
  VALUES(p_client,actor,p_key,op,p_fingerprint,result);
  RETURN result;
 END IF;

 BEGIN
  target:=(p_body->>'task_id')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN
  RAISE EXCEPTION 'hr_task_unavailable' USING ERRCODE='23514';
 END;
 SELECT t.process_id INTO pid FROM public.hr_process_tasks t WHERE t.client_id=p_client AND t.id=target;
 IF target IS NULL OR NOT FOUND THEN RAISE EXCEPTION 'hr_task_unavailable' USING ERRCODE='23514'; END IF;
 -- The process, then the task: the order of hr_process_settle and the 0632 functions.
 SELECT * INTO p FROM public.hr_processes x WHERE x.client_id=p_client AND x.id=pid FOR UPDATE;
 SELECT * INTO k FROM public.hr_process_tasks t WHERE t.client_id=p_client AND t.id=target FOR UPDATE;
 mine:=coalesce(k.assignee_employee_id=who.employee,false);
 SELECT e.manager_id INTO boss FROM public.hr_employees e WHERE e.client_id=p_client AND e.id=p.employee_id;
 -- Someone with no part in the task learns nothing about it.
 IF who.team IS NOT TRUE AND NOT mine AND (who.employee IS NULL OR boss IS DISTINCT FROM who.employee) THEN
  RAISE EXCEPTION 'hr_task_unavailable' USING ERRCODE='23514'; END IF;
 IF p.status<>'active' OR k.status IN ('done','skipped') THEN
  RAISE EXCEPTION 'hr_task_closed' USING ERRCODE='23514'; END IF;
 IF op IN ('task.skip','task.reschedule','task.reassign') AND who.team IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_people_manager_required' USING ERRCODE='42501'; END IF;
 IF op IN ('task.complete','task.note') AND who.team IS NOT TRUE AND NOT mine THEN
  RAISE EXCEPTION 'hr_task_not_assignee' USING ERRCODE='42501'; END IF;
 IF op IN ('task.approve','task.send_back') THEN
  IF k.status<>'awaiting_approval' THEN
   RAISE EXCEPTION 'hr_task_not_awaiting_approval' USING ERRCODE='23514'; END IF;
  right_:=public.hr_task_approval_right(p_client,k.id);
  IF right_ IS NULL THEN RAISE EXCEPTION 'hr_task_not_approver' USING ERRCODE='42501'; END IF;
 END IF;
 IF op<>'task.note' AND (jsonb_typeof(p_body->'expected_version') IS DISTINCT FROM 'number'
                         OR k.version::numeric<>(p_body->>'expected_version')::numeric) THEN
  RAISE EXCEPTION 'hr_task_changed' USING ERRCODE='23514'; END IF;
 IF op IN ('task.complete','task.note') AND p_body ? 'note' THEN
  IF jsonb_typeof(p_body->'note') NOT IN ('string','null') THEN
   RAISE EXCEPTION 'hr_task_note_invalid' USING ERRCODE='23514'; END IF;
  txt:=nullif(btrim(p_body->>'note'),'');
  IF char_length(txt)>2000 THEN RAISE EXCEPTION 'hr_task_note_invalid' USING ERRCODE='23514'; END IF;
  -- complete keeps the note when none is sent (or null); note always replaces it.
  has_note:=op='task.note' OR txt IS NOT NULL;
 ELSIF op='task.note' THEN
  RAISE EXCEPTION 'hr_task_note_invalid' USING ERRCODE='23514';
 END IF;
 IF op IN ('task.skip','task.send_back') AND (txt IS NULL OR char_length(txt) NOT BETWEEN 1 AND 500) THEN
  RAISE EXCEPTION 'hr_task_reason_invalid' USING ERRCODE='23514'; END IF;

 IF op='task.complete' THEN
  IF k.status='awaiting_approval' THEN
   RAISE EXCEPTION 'hr_task_awaiting_approval' USING ERRCODE='23514'; END IF;
  IF k.auto_check='access_removed' THEN
   RAISE EXCEPTION 'hr_task_system_only' USING ERRCODE='23514'; END IF;
  IF k.auto_check IS NOT NULL AND NOT public.hr_task_check(p_client,p.employee_id,k.auto_check) THEN
   RAISE EXCEPTION 'hr_task_check_not_met' USING ERRCODE='23514'; END IF;
  UPDATE public.hr_process_tasks t
  SET status=CASE WHEN k.approval_by IS NULL THEN 'done' ELSE 'awaiting_approval' END,
   completed_by=actor, completed_at=clock_timestamp(), sent_back_reason=NULL,
   note=CASE WHEN has_note THEN txt ELSE t.note END, version=t.version+1
  WHERE t.client_id=p_client AND t.id=k.id;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,pid,k.id,actor,'task.completed',
   jsonb_build_object('awaiting_approval',k.approval_by IS NOT NULL,
    'on_behalf',k.assignee_employee_id IS NOT NULL AND NOT mine)
   || CASE WHEN k.auto_check IS NOT NULL THEN jsonb_build_object('auto_check',k.auto_check)
           ELSE '{}'::jsonb END);
 ELSIF op='task.approve' THEN
  IF right_='hr_completer' THEN RAISE EXCEPTION 'hr_task_self_approval' USING ERRCODE='23514'; END IF;
  UPDATE public.hr_process_tasks t SET status='done', approved_by=actor, approved_at=clock_timestamp(),
   version=t.version+1
  WHERE t.client_id=p_client AND t.id=k.id;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,pid,k.id,actor,'task.approved',
   jsonb_build_object('approver',CASE WHEN right_='manager' THEN 'manager' ELSE 'hr' END)
   || CASE WHEN right_='hr_sole_owner' THEN jsonb_build_object('approved_without_second_person',true)
           ELSE '{}'::jsonb END);
 ELSIF op='task.send_back' THEN
  IF k.assignee_employee_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.hr_employees e
    WHERE e.client_id=p_client AND e.id=k.assignee_employee_id AND e.is_active) THEN
   -- Ledger T3 M2: never reopened for a person who has left.
   UPDATE public.hr_process_tasks t SET status='skipped', version=t.version+1
   WHERE t.client_id=p_client AND t.id=k.id;
   INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
   VALUES(p_client,pid,k.id,actor,'task.skipped',
    jsonb_build_object('reason','person_left','remark',txt,'sent_back',true));
  ELSE
   UPDATE public.hr_process_tasks t SET status='todo', completed_by=NULL, completed_at=NULL,
    sent_back_reason=txt, version=t.version+1
   WHERE t.client_id=p_client AND t.id=k.id;
   INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
   VALUES(p_client,pid,k.id,actor,'task.sent_back',jsonb_build_object('remark',txt));
  END IF;
 ELSIF op='task.note' THEN
  UPDATE public.hr_process_tasks t SET note=txt, version=t.version+1
  WHERE t.client_id=p_client AND t.id=k.id;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,pid,k.id,actor,'task.noted','{}'::jsonb);
 ELSIF op='task.skip' THEN
  UPDATE public.hr_process_tasks t SET status='skipped', version=t.version+1
  WHERE t.client_id=p_client AND t.id=k.id;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,pid,k.id,actor,'task.skipped',
   jsonb_build_object('reason','skipped_by_hr','remark',txt));
 ELSIF op='task.reschedule' THEN
  IF jsonb_typeof(p_body->'due_on') IS DISTINCT FROM 'string' THEN
   RAISE EXCEPTION 'hr_task_due_invalid' USING ERRCODE='23514'; END IF;
  BEGIN
   due:=(p_body->>'due_on')::date;
  EXCEPTION WHEN data_exception THEN
   RAISE EXCEPTION 'hr_task_due_invalid' USING ERRCODE='23514';
  END;
  UPDATE public.hr_process_tasks t SET due_on=due, due_overridden=true, version=t.version+1
  WHERE t.client_id=p_client AND t.id=k.id;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,pid,k.id,actor,'task.rescheduled',jsonb_build_object('old',k.due_on,'new',due));
 ELSE -- task.reassign
  IF NOT p_body ? 'assignee_employee_id'
   OR jsonb_typeof(p_body->'assignee_employee_id') NOT IN ('string','null') THEN
   RAISE EXCEPTION 'hr_task_assignee_unavailable' USING ERRCODE='23514'; END IF;
  BEGIN
   newly:=(p_body->>'assignee_employee_id')::uuid;
  EXCEPTION WHEN invalid_text_representation THEN
   RAISE EXCEPTION 'hr_task_assignee_unavailable' USING ERRCODE='23514';
  END;
  IF newly IS NOT NULL THEN
   PERFORM 1 FROM public.hr_employees e
    WHERE e.client_id=p_client AND e.id=newly AND e.is_active FOR SHARE;
   IF NOT FOUND THEN RAISE EXCEPTION 'hr_task_assignee_unavailable' USING ERRCODE='23514'; END IF;
  END IF;
  UPDATE public.hr_process_tasks t SET assignee_employee_id=newly, assignee_overridden=true,
   version=t.version+1
  WHERE t.client_id=p_client AND t.id=k.id;
  INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
  VALUES(p_client,pid,k.id,actor,'task.reassigned',
   jsonb_build_object('from',k.assignee_employee_id,'to',newly,'reason','reassigned_by_hr'));
 END IF;
 PERFORM public.hr_process_settle(p_client,pid);
 SELECT jsonb_build_object('operation',op,'task_id',t.id,'process_id',t.process_id,
  'task_status',t.status,'version',t.version,'process_status',x.status,'status','saved',
  'duplicate',false)
 INTO result
 FROM public.hr_process_tasks t JOIN public.hr_processes x ON x.client_id=t.client_id AND x.id=t.process_id
 WHERE t.client_id=p_client AND t.id=k.id;
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
 VALUES(p_client,actor,p_key,op,p_fingerprint,result);
 RETURN result;
END $$;

-- 0633, with the send-back reason in the own task shape (the caller's own tasks, every task for
-- HR); a manager's view of other people's tasks (the base shape) does not get it.
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
 ELSIF p.employee_id=who.employee AND p.kind IN ('onboarding','offboarding') THEN viewer:='self';
 ELSIF EXISTS(SELECT 1 FROM public.hr_process_tasks x
              WHERE x.client_id=p_client AND x.process_id=p.id
               AND x.assignee_employee_id=who.employee) THEN viewer:='assignee';
 ELSE RETURN NULL;
 END IF;
 SELECT t.name INTO tname FROM public.hr_process_templates t
  WHERE t.client_id=p_client AND t.id=p.template_id;
 header:=jsonb_build_object('process_id',p.id,'kind',p.kind,'name',tname,'status',p.status,
  'employee_name',e.display_name);
 IF viewer IN ('hr','manager','self') THEN
  header:=header||jsonb_build_object('started_at',p.started_at,'closed_at',p.closed_at);
 END IF;
 IF viewer IN ('hr','manager') THEN
  header:=header||jsonb_build_object('employee_id',p.employee_id,
   'tasks_total',(SELECT count(*) FROM public.hr_process_tasks x
                  WHERE x.client_id=p_client AND x.process_id=p.id),
   'tasks_done',(SELECT count(*) FROM public.hr_process_tasks x
                 WHERE x.client_id=p_client AND x.process_id=p.id
                  AND x.status IN ('done','skipped')));
 END IF;
 IF viewer='self' THEN
  SELECT header||jsonb_build_object('required_total',count(*) FILTER (WHERE x.required),
   'required_done',count(*) FILTER (WHERE x.required AND x.status IN ('done','skipped')),
   'open_tasks',count(*) FILTER (WHERE x.status NOT IN ('done','skipped')))
  INTO header
  FROM public.hr_process_tasks x WHERE x.client_id=p_client AND x.process_id=p.id;
 END IF;
 IF viewer='hr' THEN
  header:=header||jsonb_build_object('template_id',p.template_id,'template_version',p.template_version,
   'anchor_dates',p.anchor_dates,'started_by',p.started_by);
 END IF;
 -- Three task shapes, each built on the one before: what a manager may see of anyone's task, what
 -- an assignee sees of their own, and the HR team's whole row. The person and an assignee get
 -- only their own rows.
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
      'auto_check',x.auto_check,'reference_note',x.reference_note,'completed_at',x.completed_at,
      'note',x.note,'version',x.version,'sent_back_reason',x.sent_back_reason) AS own) own
    CROSS JOIN LATERAL (
     SELECT jsonb_build_object('assignee_employee_id',x.assignee_employee_id,
      'due_offset_days',x.due_offset_days,'due_overridden',x.due_overridden,
      'completed_by',x.completed_by,'approved_by',x.approved_by,'approved_at',x.approved_at)
      AS hr_only) hr_only
    WHERE x.client_id=p_client AND x.process_id=p.id
     AND (viewer NOT IN ('self','assignee') OR x.assignee_employee_id=who.employee)) s));
 IF viewer='hr' THEN
  result:=result||jsonb_build_object('events',
   (SELECT coalesce(jsonb_agg(jsonb_build_object('event_id',v.id,'at',v.at,'actor',v.actor,
      'action',v.action,'task_id',v.task_id,'detail',v.detail) ORDER BY v.at, v.id),'[]'::jsonb)
    FROM public.hr_process_events v WHERE v.client_id=p_client AND v.process_id=p.id));
 END IF;
 RETURN result;
END $$;

-- 0633's My tasks, plus the tasks awaiting the caller's approval (needs_my_approval) and the
-- send-back reason. A new result column cannot be added by CREATE OR REPLACE.
DROP FUNCTION IF EXISTS public.hr_task_rows_mine(uuid,text,integer,uuid);
CREATE FUNCTION public.hr_task_rows_mine(p_client uuid, p_status text, p_limit integer,
    p_after uuid)
RETURNS TABLE(task_id uuid, process_id uuid, process_kind text, process_name text,
    employee_name text, ordinal integer, title_en text, title_ms text, description_en text,
    description_ms text, owner_role text, due_on date, required boolean, approval_by text,
    auto_check text, reference_note text, status text, note text, version integer, overdue boolean,
    pool boolean, needs_my_approval boolean, sent_back_reason text)
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
  -- A manager who only approves gets what 0633's manager view shows: no descriptions, reference
  -- note, note or send-back reason (those are the assignee's and HR's).
  CASE WHEN who.team OR x.assignee_employee_id=who.employee THEN x.description_en END,
  CASE WHEN who.team OR x.assignee_employee_id=who.employee THEN x.description_ms END,
  x.owner_role, x.due_on, x.required, x.approval_by,
  x.auto_check,
  CASE WHEN who.team OR x.assignee_employee_id=who.employee THEN x.reference_note END,
  x.status,
  CASE WHEN who.team OR x.assignee_employee_id=who.employee THEN x.note END,
  x.version,
  coalesce(p.status='active' AND x.status NOT IN ('done','skipped') AND x.due_on<today,false),
  x.assignee_employee_id IS NULL,
  x.status='awaiting_approval'
   AND coalesce(public.hr_task_approval_right(p_client,x.id) IN ('hr','manager','hr_sole_owner'),false),
  CASE WHEN who.team OR x.assignee_employee_id=who.employee THEN x.sent_back_reason END
 -- Branches instead of an OR: who.team is a constant of the query, so for anyone outside the
 -- HR team the pool branch is a one-time false filter and never reads the unassigned tasks.
 FROM (SELECT y.* FROM public.hr_process_tasks y
       WHERE y.client_id=p_client AND y.assignee_employee_id=who.employee
       UNION
       SELECT y.* FROM public.hr_process_tasks y
       WHERE who.team AND y.client_id=p_client AND y.assignee_employee_id IS NULL
       UNION
       SELECT y.* FROM public.hr_process_tasks y
       WHERE y.client_id=p_client AND y.status='awaiting_approval'
        AND public.hr_task_approval_right(p_client,y.id) IN ('hr','manager','hr_sole_owner')) x
 JOIN public.hr_processes p ON p.client_id=x.client_id AND p.id=x.process_id
 JOIN public.hr_employees e ON e.client_id=p.client_id AND e.id=p.employee_id
 LEFT JOIN public.hr_process_templates t ON t.client_id=p.client_id AND t.id=p.template_id
 WHERE CASE WHEN p_status IS NULL THEN x.status NOT IN ('done','skipped') ELSE x.status=p_status END
  AND (p_after IS NULL OR (coalesce(x.due_on,'infinity'::date),x.id)>(after_due,p_after))
 ORDER BY coalesce(x.due_on,'infinity'::date), x.id
 LIMIT least(greatest(coalesce(p_limit,0),0),201);
END $$;

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_process_start(uuid,uuid,text,uuid,uuid)'::regprocedure,
  'public.hr_process_reassign_manager(uuid,uuid,uuid)'::regprocedure,
  'public.hr_process_person_left(uuid,uuid,uuid)'::regprocedure,
  'public.hr_task_check(uuid,uuid,text)'::regprocedure,
  'public.hr_task_approver(uuid,uuid)'::regprocedure,
  'public.hr_task_approval_right(uuid,uuid)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance',f);
 END LOOP;
 FOREACH f IN ARRAY ARRAY['public.hr_task_apply(uuid,uuid,text,jsonb)'::regprocedure,
  'public.hr_task_rows_mine(uuid,text,integer,uuid)'::regprocedure,
  'public.hr_process_get(uuid,uuid)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;
