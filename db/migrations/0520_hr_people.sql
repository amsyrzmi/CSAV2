-- People & HR project 1a: departments, the new employee fields, per-employee change history and the
-- People reads.
-- Adds:
--   public.hr_departments: one row per department name of a tenant. name_key folds case and runs of
--     spaces, and is unique per tenant; archived names stay taken. SELECT for csa_app through policy
--     hr_department_read (any HR session of the tenant).
--   public.hr_employees columns department_id (the department row; the department text column stays
--     as its name mirror), work_phone, start_date and employment_type.
--   public.hr_employee_changes: immutable history, one row per change to a person, holding
--     {field: {"old", "new"}} for the Overview fields (directory fields plus the HR-only region, legal
--     entity and employment category). SELECT for csa_app through policy hr_change_read (HR owner or
--     people.manage holder).
--   public.hr_department_for(p_client, p_name): internal, no EXECUTE for csa_app. Finds or creates the
--     department for a name.
--   Triggers: hr_department_canonical_name (stored names are trimmed, with whitespace runs folded
--     to one space), hr_employee_department_sync (a legacy department text write links the department row,
--     and the text always mirrors the linked name), hr_department_renamed (a rename updates the
--     mirror, never the employee version), hr_employee_record_change (writes the history row).
--   Read functions, EXECUTE to csa_app only: hr_can_manage_people, hr_people_viewer, hr_people_rows,
--     hr_department_rows, hr_employee_change_rows, hr_people_access, hr_recruitment_members.
--   The existing department text is linked to department rows before the triggers exist, so the
--   migration itself writes no history.
--   The Manage people switch: the hr_grants scope/action CHECK of 0380 becomes
--     hr_grants_scope_action, which also lets the hr_operator scope hold people.manage (no fields).
--   public.hr_people_apply(p_client, p_key, p_fingerprint, p_body): the write path beside
--     hr_admin_apply for person.add, person.update and department.save, for the HR owner or a
--     people.manage holder. Records a receipt in hr_operations, no authority event. EXECUTE to
--     csa_app only. It cannot change access, account links or whether a person is active.
--   CREATE OR REPLACE public.hr_admin_apply (0410): the same, plus the owner-only account.link.
--   CREATE OR REPLACE public.hr_apply_invitation_access (0483): the same, with p_on and p_added
--     allowed up to 16 switch ids (there are 9 now).
-- No new roles and no FORCE ROW LEVEL SECURITY. Every function is SECURITY DEFINER and pinned to
-- search_path.
-- Pre-check: 0410 (hr_is_owner, hr_private_access, hr_admin_apply), 0482 (the hr_owner_access_summary
--   JSON shapes) and 0483 (hr_apply_invitation_access) applied.
-- Rollback (in this order):
--   DROP FUNCTION public.hr_people_apply(uuid,uuid,text,jsonb);
--   Recreate public.hr_admin_apply(uuid,uuid,text,jsonb) from 0410 (CREATE OR REPLACE with the 0410
--     body) and public.hr_apply_invitation_access(uuid,uuid,jsonb,text[],text[],boolean) from 0483.
--   DELETE FROM public.hr_grants WHERE action='people.manage';
--   ALTER TABLE public.hr_grants DROP CONSTRAINT hr_grants_scope_action, ADD CONSTRAINT
--     hr_grants_check CHECK (the 0380 scope/action check: hr_operator with directory.read and
--     training.read only);
--   DROP TRIGGER hr_employee_record_change ON public.hr_employees;
--   DROP TRIGGER hr_employee_department_sync ON public.hr_employees;
--   DROP TRIGGER hr_department_renamed ON public.hr_departments;
--   DROP TRIGGER hr_department_canonical_name ON public.hr_departments;
--   DROP FUNCTION public.hr_recruitment_members(uuid); DROP FUNCTION public.hr_people_access(uuid,uuid);
--   DROP FUNCTION public.hr_employee_change_rows(uuid,uuid,integer);
--   DROP FUNCTION public.hr_department_rows(uuid); DROP FUNCTION public.hr_people_rows(uuid);
--   DROP POLICY hr_change_read ON public.hr_employee_changes;
--   DROP FUNCTION public.hr_people_viewer(uuid); DROP FUNCTION public.hr_can_manage_people(uuid);
--   DROP FUNCTION public.hr_employee_record_change(); DROP FUNCTION public.hr_department_renamed();
--   DROP FUNCTION public.hr_employee_department_sync();
--   ALTER TABLE public.hr_employees DROP COLUMN department_id, DROP COLUMN work_phone,
--     DROP COLUMN start_date, DROP COLUMN employment_type;
--   DROP FUNCTION public.hr_department_for(uuid,text); DROP FUNCTION public.hr_department_canonical_name();
--   DROP TABLE public.hr_employee_changes, public.hr_departments;
--   The department text mirror keeps the (canonical) names.

CREATE TABLE public.hr_departments(
 client_id uuid NOT NULL REFERENCES public.clients(id),
 id uuid NOT NULL,
 name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 120),
 name_key text GENERATED ALWAYS AS (lower(regexp_replace(btrim(name),'\s+',' ','g'))) STORED,
 archived_at timestamptz,
 version integer NOT NULL DEFAULT 0 CHECK(version>=0),
 PRIMARY KEY(client_id,id),
 UNIQUE(client_id,name_key));

-- A stored name is canonical: whitespace runs (tabs and newlines too) become one space, and the
-- edges are trimmed. So name_key is always lower(name), whichever path wrote the row.
CREATE FUNCTION public.hr_department_canonical_name() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 NEW.name:=btrim(regexp_replace(NEW.name,'\s+',' ','g'));
 RETURN NEW;
END $$;
CREATE TRIGGER hr_department_canonical_name BEFORE INSERT OR UPDATE OF name ON public.hr_departments
 FOR EACH ROW EXECUTE FUNCTION public.hr_department_canonical_name();

ALTER TABLE public.hr_employees
 ADD COLUMN department_id uuid,
 ADD COLUMN work_phone text CHECK(work_phone ~ '^[0-9+() -]{3,40}$'),
 ADD COLUMN start_date date,
 ADD COLUMN employment_type text CHECK(employment_type IN ('full_time','part_time','contract','intern')),
 ADD CONSTRAINT hr_employees_department_fkey FOREIGN KEY(client_id,department_id)
  REFERENCES public.hr_departments(client_id,id);

CREATE TABLE public.hr_employee_changes(
 client_id uuid NOT NULL,
 employee_id uuid NOT NULL,
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 changed_by uuid REFERENCES public.console_users(id),
 changes jsonb NOT NULL CHECK(jsonb_typeof(changes)='object' AND changes<>'{}'::jsonb),
 PRIMARY KEY(client_id,id),
 FOREIGN KEY(client_id,employee_id) REFERENCES public.hr_employees(client_id,id));
CREATE INDEX hr_employee_changes_by_employee
 ON public.hr_employee_changes(client_id,employee_id,changed_at DESC);
CREATE TRIGGER hr_employee_changes_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
 ON public.hr_employee_changes FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();

-- The department for a name, created when missing. Internal: only the triggers and definer write
-- paths call it.
CREATE FUNCTION public.hr_department_for(p_client uuid, p_name text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE canonical text:=btrim(regexp_replace(p_name,'\s+',' ','g')); found_id uuid;
BEGIN
 IF p_client IS NULL OR canonical IS NULL OR canonical='' THEN RETURN NULL; END IF;
 INSERT INTO public.hr_departments(client_id,id,name) VALUES(p_client,gen_random_uuid(),canonical)
 ON CONFLICT (client_id,name_key) DO NOTHING;
 -- The generated name_key expression, applied to the canonical name.
 SELECT d.id INTO found_id FROM public.hr_departments d
 WHERE d.client_id=p_client AND d.name_key=lower(regexp_replace(btrim(canonical),'\s+',' ','g'));
 RETURN found_id;
END $$;

-- Link the existing department text, then make the text the linked row's name. Text made only of
-- whitespace names no department and is cleared. Afterwards the text is exactly the name of
-- department_id (NULL when NULL). No history yet. tests/test_hr_people.py runs this block.
-- data step begin
UPDATE public.hr_employees SET department_id=public.hr_department_for(client_id,department)
WHERE department IS NOT NULL AND department_id IS NULL;
UPDATE public.hr_employees e SET department=d.name FROM public.hr_departments d
WHERE d.client_id=e.client_id AND d.id=e.department_id AND e.department IS DISTINCT FROM d.name;
UPDATE public.hr_employees SET department=NULL WHERE department IS NOT NULL AND department_id IS NULL;
-- data step end

-- A department id write wins. Otherwise (an insert without an id, or an update that keeps the id)
-- the text decides: text that does not name the linked row, by key, links the department of that
-- name (hr_admin_apply and fixtures write text). Text that does (the rename mirror, a case or
-- spacing variant, an unchanged value) keeps the link. Then the text mirrors the linked row's name.
CREATE FUNCTION public.hr_employee_department_sync() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF (TG_OP='INSERT' AND NEW.department_id IS NULL)
  OR (TG_OP='UPDATE' AND NEW.department_id IS NOT DISTINCT FROM OLD.department_id) THEN
  IF NOT EXISTS(SELECT 1 FROM public.hr_departments d
                WHERE d.client_id=NEW.client_id AND d.id=NEW.department_id
                AND d.name_key=lower(btrim(regexp_replace(NEW.department,'\s+',' ','g')))) THEN
   NEW.department_id:=public.hr_department_for(NEW.client_id,NEW.department);
  END IF;
 END IF;
 NEW.department:=(SELECT d.name FROM public.hr_departments d
                  WHERE d.client_id=NEW.client_id AND d.id=NEW.department_id);
 RETURN NEW;
END $$;
CREATE TRIGGER hr_employee_department_sync BEFORE INSERT OR UPDATE ON public.hr_employees
 FOR EACH ROW EXECUTE FUNCTION public.hr_employee_department_sync();

-- A rename updates the mirror text. It is not a change to the people, so no version bump and no
-- history (hr_employee_record_change compares departments by id).
CREATE FUNCTION public.hr_department_renamed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 UPDATE public.hr_employees SET department=NEW.name WHERE client_id=NEW.client_id AND department_id=NEW.id;
 RETURN NULL;
END $$;
CREATE TRIGGER hr_department_renamed AFTER UPDATE OF name ON public.hr_departments
 FOR EACH ROW WHEN (OLD.name IS DISTINCT FROM NEW.name) EXECUTE FUNCTION public.hr_department_renamed();

-- One history row per insert or update that changes an Overview field, by app.hr_user_id.
-- department is compared by id and shown by name; manager by id and shown by display name.
CREATE FUNCTION public.hr_employee_record_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE o public.hr_employees%ROWTYPE; c jsonb;
BEGIN
 IF TG_OP='UPDATE' THEN o:=OLD; END IF;
 SELECT COALESCE(jsonb_object_agg(v.field,jsonb_build_object('old',v.old_value,'new',v.new_value)),'{}'::jsonb)
 INTO c
 FROM (VALUES
  ('display_name', o.display_name IS DISTINCT FROM NEW.display_name, o.display_name, NEW.display_name),
  ('job_title', o.job_title IS DISTINCT FROM NEW.job_title, o.job_title, NEW.job_title),
  ('department', o.department_id IS DISTINCT FROM NEW.department_id, o.department, NEW.department),
  ('manager', o.manager_id IS DISTINCT FROM NEW.manager_id,
   (SELECT m.display_name FROM public.hr_employees m WHERE m.client_id=NEW.client_id AND m.id=o.manager_id),
   (SELECT m.display_name FROM public.hr_employees m WHERE m.client_id=NEW.client_id AND m.id=NEW.manager_id)),
  ('work_email', o.work_email IS DISTINCT FROM NEW.work_email, o.work_email, NEW.work_email),
  ('work_phone', o.work_phone IS DISTINCT FROM NEW.work_phone, o.work_phone, NEW.work_phone),
  ('start_date', o.start_date IS DISTINCT FROM NEW.start_date,
   to_char(o.start_date,'YYYY-MM-DD'), to_char(NEW.start_date,'YYYY-MM-DD')),
  ('employment_type', o.employment_type IS DISTINCT FROM NEW.employment_type,
   o.employment_type, NEW.employment_type),
  ('active', o.is_active IS DISTINCT FROM NEW.is_active, o.is_active::text, NEW.is_active::text),
  ('region', o.region IS DISTINCT FROM NEW.region, o.region, NEW.region),
  ('legal_entity_reference', o.legal_entity_reference IS DISTINCT FROM NEW.legal_entity_reference,
   o.legal_entity_reference, NEW.legal_entity_reference),
  ('employment_category', o.employment_category IS DISTINCT FROM NEW.employment_category,
   o.employment_category, NEW.employment_category)
 ) AS v(field, changed, old_value, new_value)
 WHERE v.changed;
 IF c<>'{}'::jsonb THEN
  INSERT INTO public.hr_employee_changes(client_id,employee_id,changed_by,changes)
  VALUES(NEW.client_id,NEW.id,nullif(current_setting('app.hr_user_id',true),'')::uuid,c);
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER hr_employee_record_change AFTER INSERT OR UPDATE ON public.hr_employees
 FOR EACH ROW EXECUTE FUNCTION public.hr_employee_record_change();

-- The Manage people switch. The grant row only becomes insertable with the write path.
CREATE FUNCTION public.hr_can_manage_people(p_client uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN RETURN false; END IF;
 PERFORM 1 FROM public.hr_grants WHERE client_id=p_client
  AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid
  AND action='people.manage' AND scope='hr_operator' FOR SHARE;
 RETURN FOUND;
END $$;

-- hr: the HR owner or a people.manage holder. directory: the directory switch. self: any other HR
-- session of the tenant. NULL: no HR session, or another tenant.
CREATE FUNCTION public.hr_people_viewer(p_client uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE me uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
BEGIN
 IF me IS NULL OR p_client IS NULL
  OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN RETURN NULL; END IF;
 IF public.hr_is_owner(p_client) OR public.hr_can_manage_people(p_client) THEN RETURN 'hr'; END IF;
 PERFORM 1 FROM public.hr_grants WHERE client_id=p_client AND user_id=me
  AND action='directory.read' AND scope='hr_operator'
  AND fields @> ARRAY['assigned_manager','department','display_name','job_title','work_email'] FOR SHARE;
 IF FOUND THEN RETURN 'directory'; END IF;
 RETURN 'self';
END $$;

CREATE FUNCTION public.hr_people_rows(p_client uuid) RETURNS TABLE(
    employee_id uuid, display_name text, job_title text, department_id uuid, department text,
    manager_id uuid, work_email text, work_phone text, start_date date, employment_type text,
    active boolean, region text, legal_entity_reference text, employment_category text,
    version integer, account_user_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE viewer text:=public.hr_people_viewer(p_client);
 me uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
BEGIN
 IF viewer IS NULL THEN RETURN; END IF;
 RETURN QUERY
 SELECT e.id, e.display_name, e.job_title, e.department_id, e.department, e.manager_id, e.work_email,
  e.work_phone, e.start_date, e.employment_type, e.is_active,
  CASE WHEN viewer='hr' THEN e.region END,
  CASE WHEN viewer='hr' THEN e.legal_entity_reference END,
  CASE WHEN viewer='hr' THEN e.employment_category END,
  CASE WHEN viewer='hr' THEN e.version END,
  CASE WHEN viewer='hr' THEN a.user_id END
 FROM public.hr_employees e
 LEFT JOIN public.hr_employee_accounts a ON a.client_id=e.client_id AND a.employee_id=e.id
 WHERE e.client_id=p_client
  AND (viewer='hr' OR (e.is_active AND (viewer='directory' OR a.user_id=me)))
 ORDER BY lower(e.display_name), e.id
 LIMIT 5000;
END $$;

CREATE FUNCTION public.hr_department_rows(p_client uuid) RETURNS TABLE(
    department_id uuid, name text, archived boolean, version integer, people integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF coalesce(public.hr_people_viewer(p_client),'none') NOT IN ('hr','directory') THEN RETURN; END IF;
 RETURN QUERY
 SELECT d.id, d.name, d.archived_at IS NOT NULL, d.version,
  (SELECT count(*)::integer FROM public.hr_employees e
   WHERE e.client_id=d.client_id AND e.department_id=d.id AND e.is_active)
 FROM public.hr_departments d
 WHERE d.client_id=p_client
 ORDER BY lower(d.name), d.id
 LIMIT 500;
END $$;

-- Newest first, at most 201 rows (a page of 200 plus one to tell it was cut).
CREATE FUNCTION public.hr_employee_change_rows(p_client uuid, p_employee uuid, p_limit integer)
RETURNS TABLE(changed_at timestamptz, actor_name text, changes jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF public.hr_people_viewer(p_client) IS DISTINCT FROM 'hr' THEN RETURN; END IF;
 RETURN QUERY
 SELECT c.changed_at,
  CASE WHEN u.is_platform_admin THEN 'FourStacks admin'
       WHEN m.is_enabled AND u.is_enabled THEN u.display_name END,
  c.changes
 FROM public.hr_employee_changes c
 LEFT JOIN public.console_users u ON u.id=c.changed_by
 LEFT JOIN public.console_memberships m ON m.user_id=c.changed_by AND m.client_id=c.client_id
 WHERE c.client_id=p_client AND c.employee_id=p_employee
 ORDER BY c.changed_at DESC, c.id DESC
 LIMIT least(greatest(coalesce(p_limit,0),0),201);
END $$;

-- The person's linked console account and its rows, in the hr_owner_access_summary shapes.
-- display_name is NULL once the account is no longer an enabled member.
CREATE FUNCTION public.hr_people_access(p_client uuid, p_employee uuid) RETURNS TABLE(
    user_id uuid, display_name text, grants jsonb, workflows jsonb, areas jsonb, is_owner boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF public.hr_people_viewer(p_client) IS DISTINCT FROM 'hr' THEN RETURN; END IF;
 RETURN QUERY
 SELECT u.id, CASE WHEN m.is_enabled AND u.is_enabled THEN u.display_name END,
  COALESCE((SELECT jsonb_agg(jsonb_build_object('action',g.action,'scope',g.scope,'fields',to_jsonb(g.fields))
            ORDER BY g.action,g.scope)
            FROM public.hr_grants g WHERE g.client_id=p_client AND g.user_id=u.id),'[]'::jsonb),
  COALESCE((SELECT jsonb_agg(jsonb_build_object('domain',w.domain,'action',w.action,'scope',w.scope)
            ORDER BY w.domain,w.action,w.scope)
            FROM public.hr_workflow_grants w WHERE w.client_id=p_client AND w.user_id=u.id),'[]'::jsonb),
  COALESCE((SELECT jsonb_agg(pg.area ORDER BY pg.area)
            FROM public.hr_private_grants pg WHERE pg.client_id=p_client AND pg.user_id=u.id),'[]'::jsonb),
  EXISTS(SELECT 1 FROM public.hr_client_owners o WHERE o.client_id=p_client AND o.user_id=u.id)
 FROM public.hr_employee_accounts a
 JOIN public.console_users u ON u.id=a.user_id
 JOIN public.console_memberships m ON m.user_id=a.user_id AND m.client_id=a.client_id
 WHERE a.client_id=p_client AND a.employee_id=p_employee;
END $$;

-- Interviewer choices: the enabled members, for the HR owner and recruitment delegates.
CREATE FUNCTION public.hr_recruitment_members(p_client uuid) RETURNS TABLE(user_id uuid, display_name text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF NOT (public.hr_is_owner(p_client) OR public.hr_private_access(p_client,'recruitment')) THEN RETURN; END IF;
 RETURN QUERY
 SELECT u.id, u.display_name FROM public.console_users u
 JOIN public.console_memberships m ON m.user_id=u.id
 WHERE m.client_id=p_client AND m.is_enabled AND u.is_enabled
 ORDER BY lower(u.display_name), u.id
 LIMIT 5000;
END $$;

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['hr_departments','hr_employee_changes'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance',t);
 END LOOP;
END $$;
GRANT SELECT ON public.hr_departments TO csa_app;
CREATE POLICY hr_department_read ON public.hr_departments FOR SELECT TO csa_app USING(
 client_id=nullif(current_setting('app.client_id',true),'')::uuid
 AND nullif(current_setting('app.hr_user_id',true),'') IS NOT NULL);
GRANT SELECT ON public.hr_employee_changes TO csa_app;
CREATE POLICY hr_change_read ON public.hr_employee_changes FOR SELECT TO csa_app USING(
 public.hr_is_owner(client_id) OR public.hr_can_manage_people(client_id));

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_department_for(uuid,text)'::regprocedure,
  'public.hr_employee_department_sync()'::regprocedure,'public.hr_department_renamed()'::regprocedure,
  'public.hr_employee_record_change()'::regprocedure,
  'public.hr_department_canonical_name()'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance',f);
 END LOOP;
 FOREACH f IN ARRAY ARRAY['public.hr_can_manage_people(uuid)'::regprocedure,
  'public.hr_people_viewer(uuid)'::regprocedure,'public.hr_people_rows(uuid)'::regprocedure,
  'public.hr_department_rows(uuid)'::regprocedure,
  'public.hr_employee_change_rows(uuid,uuid,integer)'::regprocedure,
  'public.hr_people_access(uuid,uuid)'::regprocedure,'public.hr_recruitment_members(uuid)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;

-- ---- The Manage people switch and the write paths ----------------------------------------------

-- hr_grants: the 0380 scope/action CHECK, widened so the hr_operator scope may hold people.manage.
-- Found by its definition (the only CHECK naming client_owner); the fields CHECK, which names
-- assigned_manager too, is left alone. Skipped once hr_grants_scope_action exists.
DO $$ DECLARE found_name name; matches integer; BEGIN
 IF EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.hr_grants'::regclass
           AND conname='hr_grants_scope_action') THEN RETURN; END IF;
 SELECT count(*), min(conname::text) INTO matches, found_name FROM pg_constraint
  WHERE conrelid='public.hr_grants'::regclass AND contype='c'
  AND pg_get_constraintdef(oid) LIKE '%client_owner%';
 IF matches<>1 THEN
  RAISE EXCEPTION 'hr_grants scope/action check: expected 1 match, found %', matches; END IF;
 EXECUTE format('ALTER TABLE public.hr_grants DROP CONSTRAINT %I', found_name);
 ALTER TABLE public.hr_grants ADD CONSTRAINT hr_grants_scope_action CHECK (
  (scope='self' AND action IN ('directory.read','training.read','training.draft',
                               'training.submit','training.withdraw'))
  OR (scope='assigned_manager' AND action IN ('directory.read','training.read','training.decide'))
  OR (scope='hr_operator' AND action IN ('directory.read','training.read','people.manage'))
  OR (scope='client_owner' AND action='training.decide'));
END $$;

-- The invitation apply, as 0483, with room for 16 switch ids (the Manage people switch made 9).
CREATE OR REPLACE FUNCTION public.hr_apply_invitation_access(
    p_client uuid, p_token uuid, p_rows jsonb, p_on text[], p_added text[], p_owner_override boolean)
    RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 -- The Staff preset's (action, scope) grant pairs and (domain, action, scope) workflow rows: the only
 -- SQL copy of rows(PRESETS["staff"], EMPTY) in csa/hr/access_switches.py. Staff has no areas.
 -- tests/test_hr_invitation_access.py compares the two.
 staff constant jsonb := '{"grants":[["directory.read","hr_operator"],["directory.read","self"],["training.draft","self"],["training.read","self"],["training.submit","self"],["training.withdraw","self"]],"workflows":[["attendance","prepare","self"],["attendance","read","self"],["leave","prepare","self"],["leave","read","self"]]}';
 actor uuid := nullif(current_setting('app.hr_user_id',true),'')::uuid;
 tok record; owner_chooser boolean; approver uuid; item jsonb; result jsonb;
BEGIN
 -- Every guard is written so a NULL argument can never skip it (three-valued logic).
 IF p_client IS NULL OR p_token IS NULL
  OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN
  RAISE EXCEPTION 'hr_invitation_unavailable' USING ERRCODE='23514'; END IF;
 IF p_owner_override IS NULL OR jsonb_typeof(p_rows) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_rows->'grants') IS DISTINCT FROM 'array'
  OR jsonb_typeof(p_rows->'workflows') IS DISTINCT FROM 'array'
  OR jsonb_typeof(p_rows->'areas') IS DISTINCT FROM 'array'
  OR p_on IS NULL OR p_added IS NULL OR cardinality(p_on)>16 OR cardinality(p_added)>16 THEN
  RAISE EXCEPTION 'hr_invitation_arguments_invalid' USING ERRCODE='23514'; END IF;
 IF p_owner_override IS TRUE AND public.hr_is_owner(p_client) IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_owner_required' USING ERRCODE='42501'; END IF;
 -- The token row lock serialises apply and dismiss for one invitation.
 SELECT t.user_id, t.hr_preset, t.hr_preset_chosen_by INTO tok FROM public.console_tokens t
  WHERE t.id=p_token AND t.kind='invite' AND t.client_id=p_client
  AND t.consumed_at IS NOT NULL AND t.revoked_at IS NULL AND t.hr_preset IS NOT NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'hr_invitation_unavailable' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM public.console_memberships m JOIN public.console_users u ON u.id=m.user_id
  WHERE m.client_id=p_client AND m.user_id=tok.user_id AND m.is_enabled AND u.is_enabled FOR SHARE OF m;
 IF NOT FOUND THEN RAISE EXCEPTION 'hr_invitation_unavailable' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.hr_operations op WHERE op.client_id=p_client AND op.request_key=p_token
           AND op.operation IN ('invitation_access','invitation_access.dismiss')) THEN
  RAISE EXCEPTION 'hr_invitation_applied' USING ERRCODE='23514'; END IF;
 -- The chooser counts as the HR owner only while still an enabled user with an enabled membership.
 owner_chooser := EXISTS(SELECT 1 FROM public.hr_client_owners o JOIN public.console_users c ON c.id=o.user_id
  JOIN public.console_memberships cm ON cm.user_id=o.user_id AND cm.client_id=p_client
  WHERE o.client_id=p_client AND o.user_id=tok.hr_preset_chosen_by AND NOT c.is_platform_admin
  AND c.is_enabled AND cm.is_enabled FOR SHARE OF o, cm);
 IF owner_chooser IS NOT TRUE AND p_owner_override IS NOT TRUE THEN
  IF tok.hr_preset IS DISTINCT FROM 'staff' THEN
   RAISE EXCEPTION 'hr_invitation_owner_required' USING ERRCODE='42501'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_rows->'grants') g
            WHERE NOT staff->'grants' @> jsonb_build_array(jsonb_build_array(g->>'action',g->>'scope')))
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_rows->'workflows') w
            WHERE NOT staff->'workflows' @> jsonb_build_array(
             jsonb_build_array(w->>'domain',w->>'action',w->>'scope')))
   OR jsonb_array_length(p_rows->'areas')>0 THEN
   RAISE EXCEPTION 'hr_invitation_rows_refused' USING ERRCODE='42501'; END IF;
 END IF;
 -- Add, never remove: the result is the union of the member's rows and p_rows, so access the HR
 -- owner already gave is kept. A grant for the same (action, scope) keeps the union of its fields.
 FOR item IN SELECT * FROM jsonb_array_elements(p_rows->'grants') LOOP
  INSERT INTO public.hr_grants AS h(client_id,user_id,action,scope,fields)
  VALUES(p_client,tok.user_id,item->>'action',item->>'scope',
         ARRAY(SELECT jsonb_array_elements_text(COALESCE(item->'fields','[]'::jsonb))))
  ON CONFLICT (client_id,user_id,action,scope) DO UPDATE
  SET fields=ARRAY(SELECT DISTINCT f FROM unnest(h.fields||EXCLUDED.fields) AS f ORDER BY f);
 END LOOP;
 FOR item IN SELECT * FROM jsonb_array_elements(p_rows->'workflows') LOOP
  INSERT INTO public.hr_workflow_grants(client_id,user_id,domain,action,scope)
  VALUES(p_client,tok.user_id,item->>'domain',item->>'action',item->>'scope') ON CONFLICT DO NOTHING;
 END LOOP;
 -- Areas reach this point only for an owner chooser or an owner override, both members.
 approver := CASE WHEN p_owner_override THEN actor ELSE tok.hr_preset_chosen_by END;
 FOR item IN SELECT * FROM jsonb_array_elements(p_rows->'areas') LOOP
  INSERT INTO public.hr_private_grants(client_id,user_id,area,approved_by)
  VALUES(p_client,tok.user_id,item#>>'{}',approver) ON CONFLICT DO NOTHING;
 END LOOP;
 result := jsonb_build_object('operation','invitation_access','resource_id',tok.user_id,'version',0,
  'status','saved','duplicate',false,'preset',tok.hr_preset,
  'applied_by',CASE WHEN p_owner_override THEN actor END);
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
 VALUES(p_client,tok.user_id,p_token,'invitation_access',
        encode(sha256(convert_to(p_rows::text,'UTF8')),'hex'),result);
 INSERT INTO public.hr_authority_events(client_id,subject_id,request_key,operation,details)
 VALUES(p_client,tok.user_id,p_token,'invitation_access',jsonb_build_object(
  'user_id',tok.user_id,'preset',tok.hr_preset,'on',to_jsonb(p_on),'added',to_jsonb(p_added),
  'chosen_by',tok.hr_preset_chosen_by));
 RETURN result;
END $$;


-- hr_admin_apply, as 0410, plus the owner-only account.link (the access page's Recent changes
-- leaves it out: it lists access operations only).
CREATE OR REPLACE FUNCTION public.hr_admin_apply(p_client uuid,p_key uuid,p_fingerprint text,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE actor uuid; target uuid; manager uuid; result jsonb; v integer; item jsonb; op text;
BEGIN
 actor:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 IF NOT public.hr_is_owner(p_client) THEN RAISE EXCEPTION 'hr_owner_required' USING ERRCODE='42501'; END IF;
 op:=p_body->>'operation';
 IF op='employee.save' THEN
  target:=(p_body->>'employee_id')::uuid;
  manager:=(p_body->'values'->'assigned_manager'->>'employee_id')::uuid;
  IF manager IS NOT NULL AND (p_body->'values'->'assigned_manager'->>'tenant_id')::uuid IS DISTINCT FROM p_client
   THEN RAISE EXCEPTION 'hr_cross_tenant' USING ERRCODE='23514'; END IF;
  IF p_body->>'account_id' IS NOT NULL THEN
   PERFORM 1 FROM public.console_memberships WHERE client_id=p_client AND user_id=(p_body->>'account_id')::uuid AND is_enabled FOR SHARE;
   IF NOT FOUND THEN RAISE EXCEPTION 'hr_member_unavailable' USING ERRCODE='23514'; END IF;
  END IF;
  IF p_body->>'expected_version' IS NULL THEN
   INSERT INTO public.hr_employees(client_id,id,display_name,job_title,department,work_email,manager_id,is_active,region,legal_entity_reference,employment_category)
   VALUES(p_client,target,p_body->'values'->>'display_name',p_body->'values'->>'job_title',p_body->'values'->>'department',p_body->'values'->>'work_email',manager,(p_body->>'active')::boolean,p_body->>'region',p_body->>'legal_entity_reference',p_body->>'employment_category') RETURNING version INTO v;
  ELSE
   UPDATE public.hr_employees SET display_name=p_body->'values'->>'display_name',job_title=p_body->'values'->>'job_title',department=p_body->'values'->>'department',work_email=p_body->'values'->>'work_email',manager_id=manager,is_active=(p_body->>'active')::boolean,version=version+1,region=p_body->>'region',legal_entity_reference=p_body->>'legal_entity_reference',employment_category=p_body->>'employment_category'
    WHERE client_id=p_client AND id=target AND version=(p_body->>'expected_version')::integer RETURNING version INTO v;
   IF NOT FOUND THEN RAISE EXCEPTION 'hr_stale_employee' USING ERRCODE='40001'; END IF;
  END IF;
  DELETE FROM public.hr_employee_accounts WHERE client_id=p_client AND employee_id=target;
  IF p_body->>'account_id' IS NOT NULL THEN
   INSERT INTO public.hr_employee_accounts(client_id,user_id,employee_id) VALUES(p_client,(p_body->>'account_id')::uuid,target);
  END IF;
 ELSIF op='account.link' THEN
  -- 0520: link a person to an enabled member's account (or unlink with a null account_id).
  -- An account belongs to one person at most. The receipt version is the person's version.
  target:=(p_body->>'employee_id')::uuid;
  SELECT e.version INTO v FROM public.hr_employees e WHERE e.client_id=p_client AND e.id=target FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514'; END IF;
  IF p_body->>'account_id' IS NOT NULL THEN
   PERFORM 1 FROM public.console_memberships WHERE client_id=p_client AND user_id=(p_body->>'account_id')::uuid AND is_enabled FOR SHARE;
   IF NOT FOUND THEN RAISE EXCEPTION 'hr_member_unavailable' USING ERRCODE='23514'; END IF;
   PERFORM 1 FROM public.hr_employee_accounts WHERE client_id=p_client AND user_id=(p_body->>'account_id')::uuid
    AND employee_id IS DISTINCT FROM target FOR UPDATE;
   IF FOUND THEN RAISE EXCEPTION 'hr_account_taken' USING ERRCODE='23514'; END IF;
  END IF;
  DELETE FROM public.hr_employee_accounts WHERE client_id=p_client AND employee_id=target;
  IF p_body->>'account_id' IS NOT NULL THEN
   INSERT INTO public.hr_employee_accounts(client_id,user_id,employee_id) VALUES(p_client,(p_body->>'account_id')::uuid,target);
  END IF;
 ELSE
  target:=(p_body->>'user_id')::uuid;
  PERFORM 1 FROM public.console_memberships WHERE client_id=p_client AND user_id=target AND is_enabled FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'hr_member_unavailable' USING ERRCODE='23514'; END IF;
  IF op='access.save' THEN
   DELETE FROM public.hr_grants WHERE client_id=p_client AND user_id=target;
   DELETE FROM public.hr_workflow_grants WHERE client_id=p_client AND user_id=target;
   FOR item IN SELECT * FROM jsonb_array_elements(p_body->'grants') LOOP
    INSERT INTO public.hr_grants(client_id,user_id,action,scope,fields) VALUES(p_client,target,item->>'action',item->>'scope',ARRAY(SELECT jsonb_array_elements_text(item->'fields')));
   END LOOP;
   FOR item IN SELECT * FROM jsonb_array_elements(p_body->'workflows') LOOP
    INSERT INTO public.hr_workflow_grants(client_id,user_id,domain,action,scope) VALUES(p_client,target,item->>'domain',item->>'action',item->>'scope');
   END LOOP;
  ELSIF op='private_access.save' THEN
   IF (p_body->>'enabled')::boolean THEN
    INSERT INTO public.hr_private_grants VALUES(p_client,target,p_body->>'area',actor) ON CONFLICT DO NOTHING;
   ELSE DELETE FROM public.hr_private_grants WHERE client_id=p_client AND user_id=target AND area=p_body->>'area'; END IF;
  ELSE RAISE EXCEPTION 'hr_operation_unsupported' USING ERRCODE='23514'; END IF;
  v:=0;
 END IF;
 result:=jsonb_build_object('operation',op,'resource_id',target,'version',v,'status','saved','duplicate',false);
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt) VALUES(p_client,actor,p_key,op,p_fingerprint,result);
 INSERT INTO public.hr_authority_events(client_id,subject_id,request_key,operation,details)
 VALUES(p_client,actor,p_key,op,CASE WHEN op='employee.save' THEN jsonb_build_object(
  'employee_id',target,'manager_id',manager,'account_id',p_body->'account_id','active',p_body->'active','version',v)
 WHEN op='account.link' THEN jsonb_build_object('employee_id',target,'account_id',p_body->'account_id')
 ELSE p_body END);
 RETURN result;
END $$;

-- The people write path beside hr_admin_apply: person.add, person.update and department.save, for
-- the HR owner or a people.manage holder. It never touches access, account links or is_active
-- of an existing person, and records a receipt but no authority event.
CREATE FUNCTION public.hr_people_apply(p_client uuid, p_key uuid, p_fingerprint text, p_body jsonb)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 op text:=p_body->>'operation'; vals jsonb:=p_body->'values'; prof jsonb:=p_body->'employment_profile';
 cur public.hr_employees%ROWTYPE; target uuid; dept uuid; manager uuid; manager_active boolean;
 dept_archived timestamptz; dept_version integer; v integer; result jsonb;
BEGIN
 -- Written so a NULL can never skip a guard (three-valued logic).
 IF public.hr_is_owner(p_client) IS NOT TRUE AND public.hr_can_manage_people(p_client) IS NOT TRUE THEN
  RAISE EXCEPTION 'hr_people_manager_required' USING ERRCODE='42501'; END IF;
 IF op IN ('person.add','person.update') THEN
  target:=(p_body->>'employee_id')::uuid;
  IF target IS NULL OR jsonb_typeof(vals) IS DISTINCT FROM 'object' THEN
   RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514'; END IF;
  IF op='person.update' THEN
   SELECT * INTO cur FROM public.hr_employees e WHERE e.client_id=p_client AND e.id=target FOR UPDATE;
   IF NOT FOUND THEN RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514'; END IF;
   IF cur.version IS DISTINCT FROM (p_body->>'expected_version')::integer THEN
    RAISE EXCEPTION 'hr_person_changed' USING ERRCODE='23514'; END IF;
  ELSE
   PERFORM 1 FROM public.hr_employees e WHERE e.client_id=p_client AND e.id=target;
   IF FOUND THEN RAISE EXCEPTION 'hr_employee_exists' USING ERRCODE='23514'; END IF;
  END IF;
  -- Department: an existing one of this tenant, or a typed name (found the way department names
  -- fold, or created). An archived one is refused unless it is the person's current department.
  dept:=(vals->>'department_id')::uuid;
  IF dept IS NOT NULL AND vals->>'new_department' IS NOT NULL THEN
   RAISE EXCEPTION 'hr_department_unavailable' USING ERRCODE='23514'; END IF;
  IF dept IS NOT NULL THEN
   SELECT d.archived_at INTO dept_archived FROM public.hr_departments d
    WHERE d.client_id=p_client AND d.id=dept FOR SHARE;
   IF NOT FOUND THEN RAISE EXCEPTION 'hr_department_unavailable' USING ERRCODE='23514'; END IF;
  ELSIF vals->>'new_department' IS NOT NULL THEN
   dept:=public.hr_department_for(p_client,vals->>'new_department');
   SELECT d.archived_at INTO dept_archived FROM public.hr_departments d
    WHERE d.client_id=p_client AND d.id=dept FOR SHARE;
  END IF;
  IF dept IS NOT NULL AND dept_archived IS NOT NULL AND dept IS DISTINCT FROM cur.department_id THEN
   RAISE EXCEPTION 'hr_department_archived' USING ERRCODE='23514'; END IF;
  -- Manager: none (top of company), or an employee of this tenant who is active, unless they are
  -- the person's current manager. Never the person, nor anyone who reports up to the person.
  manager:=(vals->>'manager_id')::uuid;
  IF manager IS NOT NULL THEN
   SELECT e.is_active INTO manager_active FROM public.hr_employees e
    WHERE e.client_id=p_client AND e.id=manager FOR SHARE;
   IF NOT FOUND OR (manager_active IS NOT TRUE AND manager IS DISTINCT FROM cur.manager_id) THEN
    RAISE EXCEPTION 'hr_manager_unavailable' USING ERRCODE='23514'; END IF;
   IF manager=target OR EXISTS(
     WITH RECURSIVE chain(id, depth) AS (
      SELECT manager, 1
      UNION ALL
      SELECT e.manager_id, c.depth+1 FROM chain c
      JOIN public.hr_employees e ON e.client_id=p_client AND e.id=c.id
      WHERE e.manager_id IS NOT NULL AND c.depth<500)
     SELECT 1 FROM chain WHERE chain.id=target) THEN
    RAISE EXCEPTION 'hr_manager_loop' USING ERRCODE='23514'; END IF;
  END IF;
  -- A null (or absent) employment profile clears all three columns.
  IF jsonb_typeof(prof) IS DISTINCT FROM 'object' THEN prof:='{}'::jsonb; END IF;
  IF op='person.add' THEN
   INSERT INTO public.hr_employees(client_id,id,display_name,job_title,department_id,manager_id,
    work_email,work_phone,start_date,employment_type,is_active,region,legal_entity_reference,
    employment_category)
   VALUES(p_client,target,vals->>'display_name',vals->>'job_title',dept,manager,vals->>'work_email',
    vals->>'work_phone',(vals->>'start_date')::date,vals->>'employment_type',true,prof->>'region',
    prof->>'legal_entity_reference',prof->>'employment_category')
   RETURNING version INTO v;
  ELSE
   UPDATE public.hr_employees e SET display_name=vals->>'display_name',job_title=vals->>'job_title',
    department_id=dept,manager_id=manager,work_email=vals->>'work_email',work_phone=vals->>'work_phone',
    start_date=(vals->>'start_date')::date,employment_type=vals->>'employment_type',
    region=prof->>'region',legal_entity_reference=prof->>'legal_entity_reference',
    employment_category=prof->>'employment_category',version=e.version+1
   WHERE e.client_id=p_client AND e.id=target RETURNING e.version INTO v;
  END IF;
 ELSIF op='department.save' THEN
  target:=(p_body->>'department_id')::uuid;
  IF target IS NULL THEN RAISE EXCEPTION 'hr_department_unavailable' USING ERRCODE='23514'; END IF;
  IF p_body->>'expected_version' IS NULL THEN
   -- A create. The id already in use means someone else saved it first.
   PERFORM 1 FROM public.hr_departments d WHERE d.client_id=p_client AND d.id=target;
   IF FOUND THEN RAISE EXCEPTION 'hr_department_changed' USING ERRCODE='23514'; END IF;
  ELSE
   SELECT d.version INTO dept_version FROM public.hr_departments d
    WHERE d.client_id=p_client AND d.id=target FOR UPDATE;
   IF NOT FOUND THEN RAISE EXCEPTION 'hr_department_unavailable' USING ERRCODE='23514'; END IF;
   IF dept_version IS DISTINCT FROM (p_body->>'expected_version')::integer THEN
    RAISE EXCEPTION 'hr_department_changed' USING ERRCODE='23514'; END IF;
  END IF;
  -- The name is canonicalised by hr_department_canonical_name; the unique name_key (archived
  -- names stay taken) decides whether another department already has it.
  BEGIN
   IF p_body->>'expected_version' IS NULL THEN
    INSERT INTO public.hr_departments(client_id,id,name,archived_at)
    VALUES(p_client,target,p_body->>'name',
     CASE WHEN coalesce((p_body->>'archived')::boolean,false) THEN clock_timestamp() END)
    RETURNING version INTO v;
   ELSE
    UPDATE public.hr_departments d SET name=p_body->>'name',
     archived_at=CASE WHEN coalesce((p_body->>'archived')::boolean,false)
                      THEN coalesce(d.archived_at,clock_timestamp()) END,
     version=d.version+1
    WHERE d.client_id=p_client AND d.id=target RETURNING d.version INTO v;
   END IF;
  EXCEPTION WHEN unique_violation THEN
   RAISE EXCEPTION 'hr_department_name_taken' USING ERRCODE='23514';
  END;
 ELSE RAISE EXCEPTION 'hr_operation_unsupported' USING ERRCODE='23514'; END IF;
 result:=jsonb_build_object('operation',op,'resource_id',target,'version',v,'status','saved','duplicate',false);
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
 VALUES(p_client,actor,p_key,op,p_fingerprint,result);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.hr_people_apply(uuid,uuid,text,jsonb)
 FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance;
GRANT EXECUTE ON FUNCTION public.hr_people_apply(uuid,uuid,text,jsonb) TO csa_app;
