-- HR processes, part 6: task reminders (spec 5.4, 5.7.1, 5.7.9 and section 8).
-- Adds:
--   hr_notifications_kind_check also allows task_assigned, task_due_soon, task_due and task_overdue.
--     As 0611: the allowed values are read from the constraint as the database stores it
--     (pg_get_constraintdef), the four are added when missing, and the constraint is built again
--     from that list, so a kind this migration has never heard of is kept. When all four are there
--     the constraint is left alone.
--   public.hr_notifications.due_on date NULL (the task's due date when the reminder was written) and
--     subject text NULL (1-200 characters: the plain line Home shows, "Due today: <step title>").
--     Both stay NULL for the earlier kinds. resource_id carries the task id for the task kinds.
--   public.hr_task_reminder_user(p_client, p_employee): the console user who gets the reminders of
--     a step assigned to that person: the active person's linked account, when its membership in
--     this company is enabled and not read-only and the account itself is enabled. NULL otherwise
--     (no account, a disabled leaver membership, a read-only member): the HR pool gets them.
--     Internal, no grant to any application role.
--   public.hr_tasks_due(p_client DEFAULT NULL, p_on DEFAULT NULL): the reminders due on p_on
--     (default today in Asia/Kuala_Lumpur, as hr_leavers_due) and not written yet, at most 5000:
--     (client_id, task_id, recipient_id, kind, reminder_key, due_on, title). Only open steps
--     (todo, rejected) of active processes: done, skipped and awaiting approval steps, and every
--     step of a completed or cancelled process, get none. Kinds: task_assigned once per assignee
--     (or once for the pool), task_due_soon once per due date when it is 1 or 2 days away,
--     task_due on the day, task_overdue on each of the 3 days after it, then every 7 days (day
--     10, 17, ...); a round that missed days writes one, not one per missed day. Recipients: the
--     assignee's user (hr_task_reminder_user), else (the HR pool, or an assignee who cannot get
--     them) one row per HR team member: the HR owner and every people.manage holder whose
--     membership is enabled and not read-only and whose account is enabled. reminder_key is
--     task:<task>:<reminder>:<recipient>. Every tenant's for the worker (no app.client_id), or one
--     tenant's: a session with app.client_id set sees only that tenant, whatever p_client it
--     passes. title (the step's English title) only in a session with app.client_id set, NULL in
--     the worker's every-tenant read. EXECUTE to csa_app only.
--   public.hr_task_reminder_put(p_client, p_recipient, p_kind, p_task, p_key, p_due_on, p_subject):
--     writes one reminder, true when written, false when (company, recipient, key) is already
--     there. The system only: p_client must be app.client_id (else hr_reminder_unavailable) and no
--     HR user may be set (hr_reminder_system_only); the kind one of the four task kinds
--     (hr_reminder_kind_invalid); key and subject 1-200 characters after trimming
--     (hr_reminder_invalid); the task of this company (hr_reminder_unavailable); the recipient an
--     enabled member of this company with an enabled account (hr_reminder_recipient_unavailable).
--     csa.hr.reminders.emit is its only caller. EXECUTE to csa_app only.
--   Changed from 0634 (CREATE OR REPLACE, same columns, grants kept): hr_task_rows_mine also lists,
--     for the HR team, the steps assigned to a person whose reminders go to the HR pool
--     (hr_task_reminder_user is NULL: no console account, a disabled membership, read-only), with
--     pool false. So every reminder points at a step its recipient sees in My tasks.
-- No new table, role or policy. The worker's round (csa.hr.reminders.hr_tasks_round) runs every
--   900 s; "overdue" stays derived (due_on before today), nothing is marked on the task.
-- Pre-check: 0634 applied; hr_notifications_kind_check exists and is a plain list of quoted values
--   (0410); the migration stops with an error rather than guess if it is missing or reads
--   differently. ADD COLUMN without a default is a catalog change. Safe to run twice.
-- Rollback (as the database owner, after the application no longer calls them):
--   DROP FUNCTION public.hr_task_reminder_put(uuid,uuid,text,uuid,text,date,text);
--   DROP FUNCTION public.hr_tasks_due(uuid,date);
--   re-run the hr_task_rows_mine part of 0634, then DROP FUNCTION public.hr_task_reminder_user(uuid,uuid);
--   DELETE FROM public.hr_notifications WHERE kind LIKE 'task\_%';
--   ALTER TABLE public.hr_notifications DROP COLUMN due_on, DROP COLUMN subject;
--   drop hr_notifications_kind_check and add it again with the list it held before (read it with
--   pg_get_constraintdef and leave the four task kinds out); then delete the schema_migrations row
--   for 0635.

DO $$
DECLARE
  def text;
  allowed text[];
  wanted text[] := ARRAY['task_assigned', 'task_due_soon', 'task_due', 'task_overdue'];
  missing text[];
BEGIN
  SELECT pg_catalog.pg_get_constraintdef(c.oid) INTO def
    FROM pg_catalog.pg_constraint c
   WHERE c.conrelid = 'public.hr_notifications'::regclass
     AND c.conname = 'hr_notifications_kind_check';
  IF def IS NULL THEN
    RAISE EXCEPTION '0635: hr_notifications_kind_check is missing on public.hr_notifications';
  END IF;
  IF def LIKE '%''''%' OR def !~ '^CHECK \(\(kind = ANY \(ARRAY\[' THEN
    RAISE EXCEPTION '0635: hr_notifications_kind_check does not read as a plain list of values: %', def;
  END IF;
  SELECT array_agg(m[1] ORDER BY n) INTO allowed
    FROM regexp_matches(def, '''([^'']+)''', 'g') WITH ORDINALITY AS t(m, n);
  IF allowed IS NULL THEN
    RAISE EXCEPTION '0635: hr_notifications_kind_check lists no values: %', def;
  END IF;
  SELECT array_agg(w ORDER BY n) INTO missing
    FROM unnest(wanted) WITH ORDINALITY AS u(w, n)
   WHERE NOT w = ANY (allowed);
  IF missing IS NOT NULL THEN
    ALTER TABLE public.hr_notifications DROP CONSTRAINT hr_notifications_kind_check;
    EXECUTE format('ALTER TABLE public.hr_notifications'
                   ' ADD CONSTRAINT hr_notifications_kind_check CHECK (kind IN (%s))',
                   (SELECT string_agg(quote_literal(v), ', ' ORDER BY n)
                      FROM unnest(allowed || missing) WITH ORDINALITY AS u(v, n)));
  END IF;
END $$;

ALTER TABLE public.hr_notifications ADD COLUMN IF NOT EXISTS due_on date;
ALTER TABLE public.hr_notifications ADD COLUMN IF NOT EXISTS subject text
 CHECK(char_length(subject) BETWEEN 1 AND 200);

-- Who gets the reminders of a step assigned to p_employee; NULL: the HR pool does.
CREATE OR REPLACE FUNCTION public.hr_task_reminder_user(p_client uuid, p_employee uuid)
RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT a.user_id FROM public.hr_employee_accounts a
 JOIN public.hr_employees e ON e.client_id=a.client_id AND e.id=a.employee_id AND e.is_active
 JOIN public.console_memberships m ON m.client_id=a.client_id AND m.user_id=a.user_id
  AND m.is_enabled AND m.role<>'read_only'
 JOIN public.console_users u ON u.id=a.user_id AND u.is_enabled
 WHERE a.client_id=p_client AND a.employee_id=p_employee;
$$;

CREATE OR REPLACE FUNCTION public.hr_tasks_due(p_client uuid DEFAULT NULL, p_on date DEFAULT NULL)
RETURNS TABLE(client_id uuid, task_id uuid, recipient_id uuid, kind text, reminder_key text,
    due_on date, title text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
 WITH ctx AS (
  SELECT nullif(current_setting('app.client_id',true),'')::uuid AS id,
         coalesce(p_on,(now() AT TIME ZONE 'Asia/Kuala_Lumpur')::date) AS today),
 open AS (
  SELECT x.client_id, x.id, x.assignee_employee_id, x.due_on,
   CASE WHEN ctx.id IS NOT NULL THEN x.title_en END AS title,
   CASE WHEN x.assignee_employee_id IS NOT NULL
        THEN public.hr_task_reminder_user(x.client_id,x.assignee_employee_id) END AS direct,
   ctx.today - x.due_on AS late
  FROM ctx, public.hr_process_tasks x
  JOIN public.hr_processes p ON p.client_id=x.client_id AND p.id=x.process_id AND p.status='active'
  WHERE x.status IN ('todo','rejected')
   AND (p_client IS NULL OR x.client_id=p_client)
   AND (ctx.id IS NULL OR x.client_id=ctx.id)),
 reminders AS (
  SELECT o.*, 'task_assigned' AS kind,
   'assigned:'||coalesce(o.assignee_employee_id::text,'pool') AS bucket FROM open o
  UNION ALL
  SELECT o.*, 'task_due_soon', 'due_soon:'||o.due_on FROM open o WHERE o.late BETWEEN -2 AND -1
  UNION ALL
  SELECT o.*, 'task_due', 'due:'||o.due_on FROM open o WHERE o.late=0
  UNION ALL
  -- Days 1, 2 and 3 each; days 4-9 still count as day 3 (already written); then one a week.
  SELECT o.*, 'task_overdue', 'overdue:'||o.due_on||':'||
   CASE WHEN o.late<=3 THEN o.late::text WHEN o.late<10 THEN '3' ELSE 'w'||((o.late-3)/7) END
  FROM open o WHERE o.late>0),
 team AS (
  SELECT m.client_id, m.user_id FROM ctx, public.console_memberships m
  JOIN public.console_users u ON u.id=m.user_id AND u.is_enabled
  WHERE m.is_enabled AND m.role<>'read_only'
   AND (p_client IS NULL OR m.client_id=p_client)
   AND (ctx.id IS NULL OR m.client_id=ctx.id)
   AND (EXISTS(SELECT 1 FROM public.hr_client_owners o
               WHERE o.client_id=m.client_id AND o.user_id=m.user_id)
        OR EXISTS(SELECT 1 FROM public.hr_grants g
                  WHERE g.client_id=m.client_id AND g.user_id=m.user_id
                   AND g.action='people.manage' AND g.scope='hr_operator')))
 SELECT r.client_id, r.id, who.user_id, r.kind,
  'task:'||r.id||':'||r.bucket||':'||who.user_id, r.due_on, r.title
 FROM reminders r
 CROSS JOIN LATERAL (
  SELECT r.direct AS user_id WHERE r.direct IS NOT NULL
  UNION
  SELECT t.user_id FROM team t WHERE r.direct IS NULL AND t.client_id=r.client_id) who
 WHERE NOT EXISTS(SELECT 1 FROM public.hr_notifications n
                  WHERE n.client_id=r.client_id AND n.subject_id=who.user_id
                   AND n.reminder_key='task:'||r.id||':'||r.bucket||':'||who.user_id)
 ORDER BY r.client_id, r.id, r.kind, who.user_id
 LIMIT 5000;
$$;

CREATE OR REPLACE FUNCTION public.hr_task_reminder_put(p_client uuid, p_recipient uuid, p_kind text,
    p_task uuid, p_key text, p_due_on date, p_subject text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE n integer;
BEGIN
 IF p_client IS NULL OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN
  RAISE EXCEPTION 'hr_reminder_unavailable' USING ERRCODE='42501'; END IF;
 IF nullif(current_setting('app.hr_user_id',true),'') IS NOT NULL THEN
  RAISE EXCEPTION 'hr_reminder_system_only' USING ERRCODE='42501'; END IF;
 IF p_kind IS NULL OR p_kind NOT IN ('task_assigned','task_due_soon','task_due','task_overdue') THEN
  RAISE EXCEPTION 'hr_reminder_kind_invalid' USING ERRCODE='23514'; END IF;
 IF coalesce(char_length(btrim(p_key)),0) NOT BETWEEN 1 AND 200
  OR coalesce(char_length(btrim(p_subject)),0) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION 'hr_reminder_invalid' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.hr_process_tasks t WHERE t.client_id=p_client AND t.id=p_task) THEN
  RAISE EXCEPTION 'hr_reminder_unavailable' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.console_memberships m
               JOIN public.console_users u ON u.id=m.user_id AND u.is_enabled
               WHERE m.client_id=p_client AND m.user_id=p_recipient AND m.is_enabled) THEN
  RAISE EXCEPTION 'hr_reminder_recipient_unavailable' USING ERRCODE='23514'; END IF;
 INSERT INTO public.hr_notifications(client_id,subject_id,id,reminder_key,kind,resource_id,due_on,subject)
 VALUES(p_client,p_recipient,gen_random_uuid(),btrim(p_key),p_kind,p_task,p_due_on,btrim(p_subject))
 ON CONFLICT(client_id,subject_id,reminder_key) DO NOTHING;
 GET DIAGNOSTICS n=ROW_COUNT;
 RETURN n=1;
END $$;

-- 0634's My tasks, plus (for the HR team) the steps of a person whose reminders go to the HR pool.
-- Same columns, so CREATE OR REPLACE keeps the grants.
CREATE OR REPLACE FUNCTION public.hr_task_rows_mine(p_client uuid, p_status text, p_limit integer,
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
 -- HR team the pool branches are one-time false filters and never read the other tasks.
 FROM (SELECT y.* FROM public.hr_process_tasks y
       WHERE y.client_id=p_client AND y.assignee_employee_id=who.employee
       UNION
       SELECT y.* FROM public.hr_process_tasks y
       WHERE who.team AND y.client_id=p_client AND y.assignee_employee_id IS NULL
       UNION
       -- 0635: a step whose reminders go to the HR pool (the assignee cannot get them).
       SELECT y.* FROM public.hr_process_tasks y
       WHERE who.team AND y.client_id=p_client AND y.assignee_employee_id IS NOT NULL
        AND public.hr_task_reminder_user(p_client,y.assignee_employee_id) IS NULL
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
 EXECUTE 'REVOKE ALL ON FUNCTION public.hr_task_reminder_user(uuid,uuid)'
  ' FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance';
 FOREACH f IN ARRAY ARRAY['public.hr_tasks_due(uuid,date)'::regprocedure,
  'public.hr_task_reminder_put(uuid,uuid,text,uuid,text,date,text)'::regprocedure,
  'public.hr_task_rows_mine(uuid,text,integer,uuid)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;
