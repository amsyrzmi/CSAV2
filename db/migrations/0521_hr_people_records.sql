-- People & HR project 1b: the Sensitive details switch, personal details, documents, leaving and the
-- assistant actions.
-- Adds (each task appends its own lines here, and its objects below):
--   The Sensitive details switch: the hr_grants scope/action CHECK hr_grants_scope_action (0520) also
--     lets the hr_operator scope hold people.sensitive (no fields).
--   public.hr_can_see_sensitive(p_client): true for the HR owner or a people.sensitive holder of the
--     tenant. EXECUTE to csa_app only.
--   public.hr_employee_personal: one row per person with the personal details; the IC or passport,
--     bank account, tax, EPF and SOCSO numbers are stored as ciphertext text (encrypted by the
--     application, never decrypted here). public.hr_personal_reveals: insert-only audit of each
--     Show of a number. Neither table has a grant to csa_app; both are reached only through the
--     definer functions below.
--   public.hr_personal_access(p_client,p_employee): 'hr' (HR owner or people.sensitive holder),
--     'self' (the caller's linked employee), else NULL. hr_personal_row(p_client,p_employee): the
--     record, ciphertexts included, to the same callers. hr_personal_apply(p_client,p_key,
--     p_fingerprint,p_body): personal.save with a version check, a receipt in hr_operations and a
--     value-free history row. hr_personal_reveal(p_client,p_employee,p_field): audits and returns
--     one ciphertext. EXECUTE to csa_app only.
--   public.hr_employee_documents: one row per uploaded document (kind contract, identity,
--     certificate or other; the file itself lives in HR_UPLOAD_ROOT under storage_name). Removal is
--     soft (removed_at, removed_by). request_key is unique per tenant so a retried upload finds its
--     first row. No grant to csa_app; reached only through the definer functions below.
--   public.hr_document_access(p_client,p_employee): 'manage' (HR owner or people.manage holder),
--     'self' (the caller's linked employee), else NULL. hr_document_rows(p_client,p_employee),
--     hr_document_get(p_client,p_document): what the caller may see or download (the person: their
--     contract, identity and certificate files, not other). hr_document_add(...) and
--     hr_document_remove(p_client,p_document): HR owner and people.manage holders only; each writes
--     one value-bearing hr_employee_changes row {"document": {"old","new"}}.
--     hr_document_storage_names(p_client): the storage names still referenced, for cleanup.
--     EXECUTE to csa_app only.
-- No new roles and no FORCE ROW LEVEL SECURITY. Every function is SECURITY DEFINER and pinned to
-- search_path.
-- Pre-check: 0520 applied (hr_grants_scope_action, hr_can_manage_people).
-- Rollback (in this order; later tasks add their steps at the top, newest first):
--   DROP FUNCTION public.hr_document_storage_names(uuid);
--   DROP FUNCTION public.hr_document_remove(uuid,uuid);
--   DROP FUNCTION public.hr_document_add(uuid,uuid,uuid,uuid,text,text,text,text,integer,text);
--   DROP FUNCTION public.hr_document_get(uuid,uuid);
--   DROP FUNCTION public.hr_document_rows(uuid,uuid);
--   DROP FUNCTION public.hr_document_access(uuid,uuid);
--   DROP TABLE public.hr_employee_documents;  (the document records are lost; the files under
--     HR_UPLOAD_ROOT stay on disk, so keep a dump and the volume)
--   DROP FUNCTION public.hr_personal_reveal(uuid,uuid,text);
--   DROP FUNCTION public.hr_personal_apply(uuid,uuid,text,jsonb);
--   DROP FUNCTION public.hr_personal_row(uuid,uuid);
--   DROP FUNCTION public.hr_personal_access(uuid,uuid);
--   DROP TABLE public.hr_personal_reveals, public.hr_employee_personal;  (the personal details and
--     the reveal audit are lost; take a dump of both first)
--   DROP FUNCTION public.hr_can_see_sensitive(uuid);
--   DELETE FROM public.hr_grants WHERE action='people.sensitive';
--   ALTER TABLE public.hr_grants DROP CONSTRAINT hr_grants_scope_action, ADD CONSTRAINT
--     hr_grants_scope_action CHECK (the 0520 scope/action check: hr_operator with directory.read,
--     training.read and people.manage only).

-- ---- The Sensitive details switch ---------------------------------------------------------------

-- hr_grants: the 0520 scope/action CHECK, widened so the hr_operator scope may also hold
-- people.sensitive. The fields CHECK is left alone (it already allows fields on directory.read only).
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.hr_grants'::regclass
               AND conname='hr_grants_scope_action') THEN
  RAISE EXCEPTION 'hr_grants_scope_action (0520) is missing'; END IF;
 ALTER TABLE public.hr_grants DROP CONSTRAINT hr_grants_scope_action;
 ALTER TABLE public.hr_grants ADD CONSTRAINT hr_grants_scope_action CHECK (
  (scope='self' AND action IN ('directory.read','training.read','training.draft',
                               'training.submit','training.withdraw'))
  OR (scope='assigned_manager' AND action IN ('directory.read','training.read','training.decide'))
  OR (scope='hr_operator' AND action IN ('directory.read','training.read','people.manage',
                                         'people.sensitive'))
  OR (scope='client_owner' AND action='training.decide'));
END $$;

-- The HR owner, or a holder of the Sensitive details switch, of this tenant's session.
CREATE FUNCTION public.hr_can_see_sensitive(p_client uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN RETURN false; END IF;
 IF public.hr_is_owner(p_client) THEN RETURN true; END IF;
 PERFORM 1 FROM public.hr_grants WHERE client_id=p_client
  AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid
  AND action='people.sensitive' AND scope='hr_operator' FOR SHARE;
 RETURN FOUND;
END $$;

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_can_see_sensitive(uuid)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;

-- ---- Personal details --------------------------------------------------------------------------

-- The numbers (ic_or_passport, bank_account, tax_number, epf_number, socso_number) hold ciphertext
-- produced by the application; the database never sees a plaintext number.
CREATE TABLE public.hr_employee_personal(
 client_id uuid NOT NULL,
 employee_id uuid NOT NULL,
 date_of_birth date,
 home_address text,
 personal_phone text,
 emergency_name text,
 emergency_relationship text,
 emergency_phone text,
 bank_name text,
 ic_or_passport text,
 bank_account text,
 tax_number text,
 epf_number text,
 socso_number text,
 version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_by uuid REFERENCES public.console_users(id),
 PRIMARY KEY(client_id,employee_id),
 FOREIGN KEY(client_id,employee_id) REFERENCES public.hr_employees(client_id,id));

-- One row per Show of a number. Insert-only, like hr_employee_changes.
CREATE TABLE public.hr_personal_reveals(
 client_id uuid NOT NULL,
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 employee_id uuid NOT NULL,
 field text NOT NULL CHECK(field IN ('ic_or_passport','bank_account','tax_number','epf_number','socso_number')),
 actor uuid NOT NULL REFERENCES public.console_users(id),
 revealed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(client_id,id),
 FOREIGN KEY(client_id,employee_id) REFERENCES public.hr_employees(client_id,id));
CREATE INDEX hr_personal_reveals_by_employee
 ON public.hr_personal_reveals(client_id,employee_id,revealed_at DESC);
CREATE TRIGGER hr_personal_reveals_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
 ON public.hr_personal_reveals FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['hr_employee_personal','hr_personal_reveals'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance',t);
 END LOOP;
END $$;

-- hr: the HR owner or a Sensitive details holder (any employee of the tenant). self: the caller's
-- linked employee is p_employee. NULL: anyone else, no HR session, or another tenant.
CREATE FUNCTION public.hr_personal_access(p_client uuid, p_employee uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE me uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
BEGIN
 -- Written so a NULL can never skip a guard (three-valued logic).
 IF me IS NULL OR p_client IS NULL OR p_employee IS NULL
  OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN RETURN NULL; END IF;
 IF public.hr_can_see_sensitive(p_client) IS TRUE THEN RETURN 'hr'; END IF;
 PERFORM 1 FROM public.hr_employee_accounts a
  WHERE a.client_id=p_client AND a.user_id=me AND a.employee_id=p_employee;
 IF FOUND THEN RETURN 'self'; END IF;
 RETURN NULL;
END $$;

-- The person's record, ciphertexts included (the application masks), or no row when the caller has
-- no access. A person with no saved details yet gives a row of NULLs and a NULL version.
CREATE FUNCTION public.hr_personal_row(p_client uuid, p_employee uuid) RETURNS TABLE(
    access text, employee_id uuid, date_of_birth date, home_address text, personal_phone text,
    emergency_name text, emergency_relationship text, emergency_phone text, bank_name text,
    ic_or_passport text, bank_account text, tax_number text, epf_number text, socso_number text,
    version integer, updated_at timestamptz, updated_by uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE kind text:=public.hr_personal_access(p_client,p_employee);
BEGIN
 IF kind IS NULL THEN RETURN; END IF;
 RETURN QUERY
 SELECT kind, e.id, p.date_of_birth, p.home_address, p.personal_phone, p.emergency_name,
  p.emergency_relationship, p.emergency_phone, p.bank_name, p.ic_or_passport, p.bank_account,
  p.tax_number, p.epf_number, p.socso_number, p.version, p.updated_at, p.updated_by
 FROM public.hr_employees e
 LEFT JOIN public.hr_employee_personal p ON p.client_id=e.client_id AND p.employee_id=e.id
 WHERE e.client_id=p_client AND e.id=p_employee;
END $$;

-- personal.save. Body: {operation, employee_id, expected_version (null when no row yet),
-- values: {plain fields}, encrypted: {column: ciphertext|null}}. A key that is absent keeps the
-- stored value; null clears it. The person themself may send only the five contact fields and no
-- encrypted key. The history row names the changed fields with {"old":null,"new":null}: no value
-- is ever written to history or to the receipt. A repeated (key, fingerprint) returns the stored
-- receipt with duplicate=true; the same key with another fingerprint is hr_idempotency_conflict.
CREATE FUNCTION public.hr_personal_apply(p_client uuid, p_key uuid, p_fingerprint text, p_body jsonb)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 op text:=p_body->>'operation'; target uuid:=(p_body->>'employee_id')::uuid;
 vals jsonb:=p_body->'values'; enc jsonb:=p_body->'encrypted';
 kind text:=public.hr_personal_access(p_client,target);
 old_op public.hr_operations%ROWTYPE; cur public.hr_employee_personal%ROWTYPE; had_row boolean;
 dob date; v integer; changes jsonb; result jsonb;
BEGIN
 IF kind IS NULL THEN RAISE EXCEPTION 'hr_personal_access_required' USING ERRCODE='42501'; END IF;
 -- One bounded HR operation at a time per tenant, the lock HrOperations.apply takes first.
 PERFORM pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-operations',0));
 -- A repeated request returns the first receipt before anything else is looked at, so a retry
 -- after a lost response never meets the version check it already passed.
 SELECT * INTO old_op FROM public.hr_operations o
  WHERE o.client_id=p_client AND o.subject_id=actor AND o.request_key=p_key;
 IF FOUND THEN
  IF old_op.fingerprint IS DISTINCT FROM p_fingerprint THEN
   RAISE EXCEPTION 'hr_idempotency_conflict' USING ERRCODE='23514'; END IF;
  RETURN old_op.receipt||jsonb_build_object('duplicate',true);
 END IF;
 IF op IS DISTINCT FROM 'personal.save' THEN
  RAISE EXCEPTION 'hr_operation_unsupported' USING ERRCODE='23514'; END IF;
 IF vals IS NULL OR jsonb_typeof(vals)='null' THEN vals:='{}'::jsonb; END IF;
 IF enc IS NULL OR jsonb_typeof(enc)='null' THEN enc:='{}'::jsonb; END IF;
 -- Only known fields, each a string or null.
 IF jsonb_typeof(vals) IS DISTINCT FROM 'object' OR jsonb_typeof(enc) IS DISTINCT FROM 'object'
  OR EXISTS(SELECT 1 FROM jsonb_each(vals) f
            WHERE f.key NOT IN ('date_of_birth','home_address','personal_phone','emergency_name',
                                'emergency_relationship','emergency_phone','bank_name')
             OR jsonb_typeof(f.value) NOT IN ('string','null'))
  OR EXISTS(SELECT 1 FROM jsonb_each(enc) f
            WHERE f.key NOT IN ('ic_or_passport','bank_account','tax_number','epf_number','socso_number')
             OR jsonb_typeof(f.value) NOT IN ('string','null')) THEN
  RAISE EXCEPTION 'hr_personal_field_refused' USING ERRCODE='23514'; END IF;
 -- The person themself: the five contact fields only, and no number.
 IF kind='self' AND (enc<>'{}'::jsonb OR EXISTS(
   SELECT 1 FROM jsonb_object_keys(vals) k
   WHERE k NOT IN ('home_address','personal_phone','emergency_name','emergency_relationship',
                   'emergency_phone'))) THEN
  RAISE EXCEPTION 'hr_personal_field_refused' USING ERRCODE='23514'; END IF;
 BEGIN
  dob:=(vals->>'date_of_birth')::date;
 EXCEPTION WHEN data_exception THEN
  RAISE EXCEPTION 'hr_personal_field_refused' USING ERRCODE='23514';
 END;
 PERFORM 1 FROM public.hr_employees e WHERE e.client_id=p_client AND e.id=target FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514'; END IF;
 SELECT * INTO cur FROM public.hr_employee_personal p
  WHERE p.client_id=p_client AND p.employee_id=target FOR UPDATE;
 had_row:=FOUND;
 IF (CASE WHEN had_row THEN cur.version END) IS DISTINCT FROM (p_body->>'expected_version')::integer THEN
  RAISE EXCEPTION 'hr_person_changed' USING ERRCODE='23514'; END IF;
 -- Changed fields: a plain or encrypted key that was sent and differs from the stored value
 -- (a number is "different" when its ciphertext is), or that clears a stored value.
 SELECT COALESCE(jsonb_object_agg(c.field,jsonb_build_object('old',NULL,'new',NULL)),'{}'::jsonb)
 INTO changes
 FROM (VALUES
  ('date_of_birth', vals ? 'date_of_birth' AND dob IS DISTINCT FROM cur.date_of_birth),
  ('home_address', vals ? 'home_address' AND vals->>'home_address' IS DISTINCT FROM cur.home_address),
  ('personal_phone', vals ? 'personal_phone' AND vals->>'personal_phone' IS DISTINCT FROM cur.personal_phone),
  ('emergency_name', vals ? 'emergency_name' AND vals->>'emergency_name' IS DISTINCT FROM cur.emergency_name),
  ('emergency_relationship', vals ? 'emergency_relationship'
   AND vals->>'emergency_relationship' IS DISTINCT FROM cur.emergency_relationship),
  ('emergency_phone', vals ? 'emergency_phone' AND vals->>'emergency_phone' IS DISTINCT FROM cur.emergency_phone),
  ('bank_name', vals ? 'bank_name' AND vals->>'bank_name' IS DISTINCT FROM cur.bank_name),
  ('ic_or_passport', enc ? 'ic_or_passport' AND enc->>'ic_or_passport' IS DISTINCT FROM cur.ic_or_passport),
  ('bank_account', enc ? 'bank_account' AND enc->>'bank_account' IS DISTINCT FROM cur.bank_account),
  ('tax_number', enc ? 'tax_number' AND enc->>'tax_number' IS DISTINCT FROM cur.tax_number),
  ('epf_number', enc ? 'epf_number' AND enc->>'epf_number' IS DISTINCT FROM cur.epf_number),
  ('socso_number', enc ? 'socso_number' AND enc->>'socso_number' IS DISTINCT FROM cur.socso_number)
 ) AS c(field, changed)
 WHERE c.changed;
 v:=CASE WHEN had_row THEN cur.version ELSE 0 END;
 IF changes<>'{}'::jsonb THEN
  BEGIN
   IF had_row THEN
    UPDATE public.hr_employee_personal p SET
     date_of_birth=CASE WHEN vals ? 'date_of_birth' THEN dob ELSE p.date_of_birth END,
     home_address=CASE WHEN vals ? 'home_address' THEN vals->>'home_address' ELSE p.home_address END,
     personal_phone=CASE WHEN vals ? 'personal_phone' THEN vals->>'personal_phone' ELSE p.personal_phone END,
     emergency_name=CASE WHEN vals ? 'emergency_name' THEN vals->>'emergency_name' ELSE p.emergency_name END,
     emergency_relationship=CASE WHEN vals ? 'emergency_relationship'
      THEN vals->>'emergency_relationship' ELSE p.emergency_relationship END,
     emergency_phone=CASE WHEN vals ? 'emergency_phone' THEN vals->>'emergency_phone' ELSE p.emergency_phone END,
     bank_name=CASE WHEN vals ? 'bank_name' THEN vals->>'bank_name' ELSE p.bank_name END,
     ic_or_passport=CASE WHEN enc ? 'ic_or_passport' THEN enc->>'ic_or_passport' ELSE p.ic_or_passport END,
     bank_account=CASE WHEN enc ? 'bank_account' THEN enc->>'bank_account' ELSE p.bank_account END,
     tax_number=CASE WHEN enc ? 'tax_number' THEN enc->>'tax_number' ELSE p.tax_number END,
     epf_number=CASE WHEN enc ? 'epf_number' THEN enc->>'epf_number' ELSE p.epf_number END,
     socso_number=CASE WHEN enc ? 'socso_number' THEN enc->>'socso_number' ELSE p.socso_number END,
     version=p.version+1, updated_at=clock_timestamp(), updated_by=actor
    WHERE p.client_id=p_client AND p.employee_id=target RETURNING p.version INTO v;
   ELSE
    INSERT INTO public.hr_employee_personal(client_id,employee_id,date_of_birth,home_address,
     personal_phone,emergency_name,emergency_relationship,emergency_phone,bank_name,ic_or_passport,
     bank_account,tax_number,epf_number,socso_number,updated_by)
    VALUES(p_client,target,dob,vals->>'home_address',vals->>'personal_phone',vals->>'emergency_name',
     vals->>'emergency_relationship',vals->>'emergency_phone',vals->>'bank_name',
     enc->>'ic_or_passport',enc->>'bank_account',enc->>'tax_number',enc->>'epf_number',
     enc->>'socso_number',actor)
    RETURNING version INTO v;
   END IF;
  EXCEPTION WHEN unique_violation THEN
   -- Someone saved the first record at the same moment.
   RAISE EXCEPTION 'hr_person_changed' USING ERRCODE='23514';
  END;
  INSERT INTO public.hr_employee_changes(client_id,employee_id,changed_by,changes)
  VALUES(p_client,target,actor,changes);
 END IF;
 result:=jsonb_build_object('operation',op,'resource_id',target,'version',v,'status','saved','duplicate',false);
 INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
 VALUES(p_client,actor,p_key,op,p_fingerprint,result);
 RETURN result;
END $$;

-- One ciphertext, to the HR owner, a Sensitive details holder or the person themself, with a
-- reveal row for the audit. NULL when no number is stored (the Show is still recorded).
CREATE FUNCTION public.hr_personal_reveal(p_client uuid, p_employee uuid, p_field text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid; shown text;
BEGIN
 IF public.hr_personal_access(p_client,p_employee) IS NULL THEN
  RAISE EXCEPTION 'hr_personal_access_required' USING ERRCODE='42501'; END IF;
 IF p_field IS NULL OR p_field NOT IN ('ic_or_passport','bank_account','tax_number','epf_number','socso_number') THEN
  RAISE EXCEPTION 'hr_personal_field_refused' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM public.hr_employees e WHERE e.client_id=p_client AND e.id=p_employee FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514'; END IF;
 INSERT INTO public.hr_personal_reveals(client_id,employee_id,field,actor)
 VALUES(p_client,p_employee,p_field,actor);
 SELECT to_jsonb(p)->>p_field INTO shown FROM public.hr_employee_personal p
  WHERE p.client_id=p_client AND p.employee_id=p_employee;
 RETURN shown;
END $$;

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_personal_access(uuid,uuid)'::regprocedure,
  'public.hr_personal_row(uuid,uuid)'::regprocedure,
  'public.hr_personal_apply(uuid,uuid,text,jsonb)'::regprocedure,
  'public.hr_personal_reveal(uuid,uuid,text)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;

-- ---- Documents ---------------------------------------------------------------------------------

CREATE TABLE public.hr_employee_documents(
 client_id uuid NOT NULL,
 id uuid NOT NULL,
 employee_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('contract','identity','certificate','other')),
 file_name text NOT NULL CHECK(char_length(file_name) BETWEEN 1 AND 255),
 storage_name text NOT NULL UNIQUE
  CHECK(storage_name ~ '^[a-f0-9-]{36}\.(pdf|jpg|png|docx)$'),
 media_type text NOT NULL CHECK(media_type IN ('application/pdf','image/jpeg','image/png',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
 byte_size integer NOT NULL CHECK(byte_size BETWEEN 1 AND 10485760),
 sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
 uploaded_by uuid NOT NULL REFERENCES public.console_users(id),
 uploaded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 removed_at timestamptz,
 removed_by uuid REFERENCES public.console_users(id),
 request_key uuid NOT NULL,
 PRIMARY KEY(client_id,id),
 UNIQUE(client_id,request_key),
 CHECK((removed_at IS NULL) = (removed_by IS NULL)),
 FOREIGN KEY(client_id,employee_id) REFERENCES public.hr_employees(client_id,id));
CREATE INDEX hr_employee_documents_by_employee
 ON public.hr_employee_documents(client_id,employee_id,uploaded_at DESC);

ALTER TABLE public.hr_employee_documents ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.hr_employee_documents FROM PUBLIC,csa_app,csa_ro,csa_identity,csa_hr_maintenance;

-- manage: the HR owner or a people.manage holder (any employee of the tenant). self: the caller's
-- linked employee is p_employee. NULL: anyone else, no HR session, or another tenant.
CREATE FUNCTION public.hr_document_access(p_client uuid, p_employee uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE me uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
BEGIN
 -- Written so a NULL can never skip a guard (three-valued logic).
 IF me IS NULL OR p_client IS NULL OR p_employee IS NULL
  OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN RETURN NULL; END IF;
 IF public.hr_is_owner(p_client) IS TRUE OR public.hr_can_manage_people(p_client) IS TRUE THEN
  RETURN 'manage'; END IF;
 PERFORM 1 FROM public.hr_employee_accounts a
  WHERE a.client_id=p_client AND a.user_id=me AND a.employee_id=p_employee;
 IF FOUND THEN RETURN 'self'; END IF;
 RETURN NULL;
END $$;

-- The documents the caller may see, newest first, without removed ones. Raises when the caller has
-- no access at all, so "no access" is never mistaken for "no documents".
CREATE FUNCTION public.hr_document_rows(p_client uuid, p_employee uuid) RETURNS TABLE(
    id uuid, employee_id uuid, kind text, file_name text, media_type text, byte_size integer,
    uploaded_at timestamptz, uploaded_by_name text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE access text:=public.hr_document_access(p_client,p_employee);
BEGIN
 IF access IS NULL THEN RAISE EXCEPTION 'hr_document_access_required' USING ERRCODE='42501'; END IF;
 -- A manager of documents asking about a person who is not in this tenant gets "not found", not an
 -- empty list. (The person themself exists by their own account link.)
 IF access='manage' AND NOT EXISTS(SELECT 1 FROM public.hr_employees e
                                   WHERE e.client_id=p_client AND e.id=p_employee) THEN
  RAISE EXCEPTION 'hr_document_unavailable' USING ERRCODE='23514'; END IF;
 RETURN QUERY
 SELECT d.id, d.employee_id, d.kind, d.file_name, d.media_type, d.byte_size, d.uploaded_at,
  CASE WHEN u.is_platform_admin THEN 'FourStacks admin'
       WHEN m.is_enabled AND u.is_enabled THEN u.display_name END
 FROM public.hr_employee_documents d
 LEFT JOIN public.console_users u ON u.id=d.uploaded_by
 LEFT JOIN public.console_memberships m ON m.user_id=d.uploaded_by AND m.client_id=d.client_id
 WHERE d.client_id=p_client AND d.employee_id=p_employee AND d.removed_at IS NULL
  AND (access='manage' OR d.kind IN ('contract','identity','certificate'))
 ORDER BY d.uploaded_at DESC, d.id DESC
 LIMIT 500;
END $$;

-- One document the caller may download, with its storage name: no row when it is removed, does not
-- exist, or the caller may not see it.
CREATE FUNCTION public.hr_document_get(p_client uuid, p_document uuid) RETURNS TABLE(
    id uuid, employee_id uuid, kind text, file_name text, storage_name text, media_type text,
    byte_size integer, sha256 text, uploaded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE d public.hr_employee_documents%ROWTYPE; access text;
BEGIN
 IF p_client IS NULL OR p_document IS NULL THEN RETURN; END IF;
 SELECT * INTO d FROM public.hr_employee_documents x
  WHERE x.client_id=p_client AND x.id=p_document AND x.removed_at IS NULL;
 IF NOT FOUND THEN RETURN; END IF;
 access:=public.hr_document_access(p_client,d.employee_id);
 IF access IS NULL OR (access='self' AND d.kind NOT IN ('contract','identity','certificate')) THEN
  RETURN; END IF;
 RETURN QUERY SELECT d.id, d.employee_id, d.kind, d.file_name, d.storage_name, d.media_type,
  d.byte_size, d.sha256, d.uploaded_at;
END $$;

-- Add a document record, after the application stored the file. A repeated p_key returns the first
-- row with duplicate=true (the same key for another file or person is hr_idempotency_conflict).
-- HR owner and people.manage holders only.
CREATE FUNCTION public.hr_document_add(p_client uuid, p_key uuid, p_employee uuid, p_id uuid,
    p_kind text, p_file_name text, p_storage_name text, p_media_type text, p_size integer,
    p_sha256 text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 doc public.hr_employee_documents%ROWTYPE; made_by text;
BEGIN
 IF public.hr_document_access(p_client,p_employee) IS DISTINCT FROM 'manage' THEN
  RAISE EXCEPTION 'hr_document_access_required' USING ERRCODE='42501'; END IF;
 IF p_key IS NULL OR p_id IS NULL THEN
  RAISE EXCEPTION 'hr_document_unavailable' USING ERRCODE='23514'; END IF;
 -- One document write at a time per tenant, the lock the application takes before it stores a file.
 PERFORM pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-documents',0));
 SELECT * INTO doc FROM public.hr_employee_documents x
  WHERE x.client_id=p_client AND x.request_key=p_key;
 IF FOUND THEN
  IF doc.employee_id IS DISTINCT FROM p_employee OR doc.kind IS DISTINCT FROM p_kind
   OR doc.file_name IS DISTINCT FROM p_file_name OR doc.sha256 IS DISTINCT FROM p_sha256 THEN
   RAISE EXCEPTION 'hr_idempotency_conflict' USING ERRCODE='23514'; END IF;
 ELSE
  PERFORM 1 FROM public.hr_employees e WHERE e.client_id=p_client AND e.id=p_employee FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'hr_employee_unavailable' USING ERRCODE='23514'; END IF;
  INSERT INTO public.hr_employee_documents(client_id,id,employee_id,kind,file_name,storage_name,
   media_type,byte_size,sha256,uploaded_by,request_key)
  VALUES(p_client,p_id,p_employee,p_kind,p_file_name,p_storage_name,p_media_type,p_size,p_sha256,
   actor,p_key)
  RETURNING * INTO doc;
  INSERT INTO public.hr_employee_changes(client_id,employee_id,changed_by,changes)
  VALUES(p_client,p_employee,actor,jsonb_build_object('document',
   jsonb_build_object('old',NULL,'new',initcap(doc.kind)||': '||doc.file_name)));
 END IF;
 SELECT CASE WHEN u.is_platform_admin THEN 'FourStacks admin'
             WHEN m.is_enabled AND u.is_enabled THEN u.display_name END INTO made_by
  FROM public.console_users u
  LEFT JOIN public.console_memberships m ON m.user_id=u.id AND m.client_id=p_client
  WHERE u.id=doc.uploaded_by;
 RETURN jsonb_build_object('document_id',doc.id,'employee_id',doc.employee_id,'kind',doc.kind,
  'file_name',doc.file_name,'media_type',doc.media_type,'byte_size',doc.byte_size,
  'uploaded_at',doc.uploaded_at,'uploaded_by_name',made_by,'duplicate',(doc.id IS DISTINCT FROM p_id));
END $$;

-- Remove (soft) and return the storage name, for the file cleanup. A document that is already
-- removed returns its storage name again and writes nothing, so a retry after a lost response
-- succeeds. HR owner and people.manage holders only.
CREATE FUNCTION public.hr_document_remove(p_client uuid, p_document uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 actor uuid:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 d public.hr_employee_documents%ROWTYPE;
BEGIN
 IF p_client IS NULL OR p_document IS NULL THEN
  RAISE EXCEPTION 'hr_document_unavailable' USING ERRCODE='23514'; END IF;
 SELECT * INTO d FROM public.hr_employee_documents x
  WHERE x.client_id=p_client AND x.id=p_document;
 IF NOT FOUND THEN
  -- The same answer for a document of another tenant.
  RAISE EXCEPTION 'hr_document_unavailable' USING ERRCODE='23514'; END IF;
 IF public.hr_document_access(p_client,d.employee_id) IS DISTINCT FROM 'manage' THEN
  RAISE EXCEPTION 'hr_document_access_required' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-documents',0));
 SELECT * INTO d FROM public.hr_employee_documents x
  WHERE x.client_id=p_client AND x.id=p_document FOR UPDATE;
 IF d.removed_at IS NULL THEN
  UPDATE public.hr_employee_documents x SET removed_at=clock_timestamp(), removed_by=actor
   WHERE x.client_id=p_client AND x.id=p_document;
  INSERT INTO public.hr_employee_changes(client_id,employee_id,changed_by,changes)
  VALUES(p_client,d.employee_id,actor,jsonb_build_object('document',
   jsonb_build_object('old',initcap(d.kind)||': '||d.file_name,'new',NULL)));
 END IF;
 RETURN d.storage_name;
END $$;

-- The storage names a tenant's files may keep: those of documents not removed. Raises for another
-- tenant, so an empty answer can never come from a mismatch and make the cleanup delete everything.
CREATE FUNCTION public.hr_document_storage_names(p_client uuid) RETURNS SETOF text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS NULL OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN
  RAISE EXCEPTION 'hr_document_access_required' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT d.storage_name FROM public.hr_employee_documents d
  WHERE d.client_id=p_client AND d.removed_at IS NULL;
END $$;

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_document_access(uuid,uuid)'::regprocedure,
  'public.hr_document_rows(uuid,uuid)'::regprocedure,
  'public.hr_document_get(uuid,uuid)'::regprocedure,
  'public.hr_document_add(uuid,uuid,uuid,uuid,text,text,text,text,integer,text)'::regprocedure,
  'public.hr_document_remove(uuid,uuid)'::regprocedure,
  'public.hr_document_storage_names(uuid)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;
