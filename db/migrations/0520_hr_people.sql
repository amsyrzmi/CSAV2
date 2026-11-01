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
-- No new roles and no FORCE ROW LEVEL SECURITY. Every function is SECURITY DEFINER and pinned to
-- search_path.
-- Pre-check: 0410 (hr_is_owner, hr_private_access, hr_admin_apply), 0482 (the hr_owner_access_summary
--   JSON shapes) and 0483 (hr_apply_invitation_access) applied.
-- Rollback (in this order):
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
