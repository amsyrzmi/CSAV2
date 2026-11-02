-- Moving the HR owner: one definer function moves a company's HR owner and the owner-only rows, in
-- one transaction, and one definer read says who the owner is.
-- Adds:
--   public.hr_owner_move(p_client,p_actor,p_new_owner,p_expected_owner,p_request_key,p_fingerprint):
--     for an enabled platform admin or an enabled tenant admin of p_client. Rewrites the
--     hr_client_owners row; deletes the old owner's scope='client_owner' rows from hr_grants and
--     hr_workflow_grants, and touches nothing else of theirs; gives the new owner the standard owner
--     set (no payroll row or area, given or taken); records hr_operations, hr_authority_events and
--     console_audit 'hr.owner_moved'. A repeated (key, fingerprint) returns the first result with
--     duplicate=true. Refusals: hr_owner_move_denied (42501); hr_owner_read_only, hr_owner_leaving,
--     hr_owner_unavailable, hr_owner_unchanged, hr_owner_changed, hr_idempotency_conflict (23514).
--   public.hr_owner_read(p_client,p_actor): for the same people, the owner's user_id, display_name
--     and whether they can act (usable); no row when there is no owner.
--   EXECUTE to csa_app only. No new tables or roles and no FORCE ROW LEVEL SECURITY. Both functions
--   are SECURITY DEFINER and pinned to search_path.
-- Pre-check: 0521 applied (hr_employees.last_day, hr_finish_leaver).
-- Rollback:
--   DROP FUNCTION public.hr_owner_read(uuid,uuid);
--   DROP FUNCTION public.hr_owner_move(uuid,uuid,uuid,uuid,uuid,text);
--   (Owners already moved stay, with their rows; the hr_operations, hr_authority_events and
--   console_audit rows stay too. An image from before this feature doesn't show owner.move rows in
--   Recent changes at all, because its access_event_page.sql leaves that operation out.)

CREATE FUNCTION public.hr_owner_move(p_client uuid, p_actor uuid, p_new_owner uuid,
    p_expected_owner uuid, p_request_key uuid, p_fingerprint text)
RETURNS TABLE(old_owner uuid, new_owner uuid, duplicate boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
 holder uuid; first_run public.hr_operations%ROWTYPE; ready boolean; target_role text;
BEGIN
 -- Written so a NULL can never skip a guard (three-valued logic). No lock is taken for a session
 -- of another tenant or another user.
 IF p_client IS NULL OR p_actor IS NULL
  OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid
  OR p_actor IS DISTINCT FROM nullif(current_setting('app.hr_user_id',true),'')::uuid THEN
  RAISE EXCEPTION 'hr_owner_move_denied' USING ERRCODE='42501'; END IF;
 -- The key scripts/bootstrap_hr_owner.py takes: it also serialises a first owner, who has no row.
 PERFORM pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-owner-bootstrap',0));
 -- The current owner's requests take this row FOR SHARE before their grant rows, so this order
 -- can't deadlock with those authority readers. Employee and account rows are read without a lock:
 -- person.leave takes the employee row and then this row, so it waits for this move and then sees
 -- the new owner.
 -- One deadlock is accepted. hr_lock_authority locks this row only for the current owner, so the
 -- person being made owner can hold their own grant rows FOR SHARE without it. If that request is
 -- person.leave, it waits on this row while the directory.read upsert below waits on their grant
 -- row. Postgres aborts one side (40P01), usually this move: nothing is half written and the route
 -- answers "try again". Taking :hr-operations here or locking the target's grants first would make
 -- it worse: the first deadlocks with the owner's ordinary requests, the second with access.save.
 SELECT o.user_id INTO holder FROM public.hr_client_owners o WHERE o.client_id=p_client FOR UPDATE;
 -- The actor before the receipt: a caller without the right never reads one.
 IF NOT EXISTS(SELECT 1 FROM public.console_users u
               LEFT JOIN public.console_memberships m ON m.user_id=u.id AND m.client_id=p_client
               WHERE u.id=p_actor AND u.is_enabled
                AND (u.is_platform_admin OR (m.is_enabled AND m.role='tenant_admin'))) THEN
  RAISE EXCEPTION 'hr_owner_move_denied' USING ERRCODE='42501'; END IF;
 -- By key, not by subject: hr_operations keys on the subject, which is the person chosen, so a
 -- retry naming another person must still find the first run. The receipt comes before the owner
 -- and target checks, whose answers have changed since that run.
 SELECT * INTO first_run FROM public.hr_operations op
  WHERE op.client_id=p_client AND op.request_key=p_request_key AND op.operation='owner.move';
 IF FOUND THEN
  IF first_run.fingerprint IS DISTINCT FROM p_fingerprint THEN
   RAISE EXCEPTION 'hr_idempotency_conflict' USING ERRCODE='23514'; END IF;
  RETURN QUERY SELECT (first_run.receipt->>'old_owner')::uuid,
   (first_run.receipt->>'new_owner')::uuid, true;
  RETURN;
 END IF;
 IF p_expected_owner IS DISTINCT FROM holder THEN
  RAISE EXCEPTION 'hr_owner_changed' USING ERRCODE='23514'; END IF;
 IF p_new_owner=holder THEN
  RAISE EXCEPTION 'hr_owner_unchanged' USING ERRCODE='23514'; END IF;
 SELECT u.is_enabled AND m.is_enabled AND NOT u.must_change_password, m.role
  INTO ready, target_role
  FROM public.console_memberships m JOIN public.console_users u ON u.id=m.user_id
  WHERE m.client_id=p_client AND m.user_id=p_new_owner;
 IF ready IS NOT TRUE THEN RAISE EXCEPTION 'hr_owner_unavailable' USING ERRCODE='23514'; END IF;
 IF target_role='read_only' THEN RAISE EXCEPTION 'hr_owner_read_only' USING ERRCODE='23514'; END IF;
 -- A person record is not required (as bootstrap); a linked one must be staying.
 IF EXISTS(SELECT 1 FROM public.hr_employee_accounts a
           JOIN public.hr_employees e ON e.client_id=a.client_id AND e.id=a.employee_id
           WHERE a.client_id=p_client AND a.user_id=p_new_owner
            AND (e.is_active IS NOT TRUE OR e.last_day IS NOT NULL)) THEN
  RAISE EXCEPTION 'hr_owner_leaving' USING ERRCODE='23514'; END IF;

 INSERT INTO public.hr_client_owners(client_id,user_id,approved_by,approved_at,approval_reference)
 VALUES(p_client,p_new_owner,p_actor,clock_timestamp(),'console:'||p_request_key)
 ON CONFLICT(client_id) DO UPDATE SET user_id=EXCLUDED.user_id, approved_by=EXCLUDED.approved_by,
  approved_at=EXCLUDED.approved_at, approval_reference=EXCLUDED.approval_reference;

 -- Only the rows that count solely while holding the title. The old owner may be moving to another
 -- HR role; their other rows stay until the new owner changes them, or a leaving finish removes them.
 DELETE FROM public.hr_grants g WHERE g.client_id=p_client AND g.user_id=holder AND g.scope='client_owner';
 DELETE FROM public.hr_workflow_grants w
  WHERE w.client_id=p_client AND w.user_id=holder AND w.scope='client_owner';

 -- The standard owner set. No payroll: a new owner turns it on for themselves on the access page.
 -- tests/test_hr_owner_move.py compares this list with every client_owner combination the CHECKs allow.
 INSERT INTO public.hr_grants(client_id,user_id,action,scope)
 VALUES(p_client,p_new_owner,'training.decide','client_owner'),
       (p_client,p_new_owner,'training.read','hr_operator')
 ON CONFLICT DO NOTHING;
 INSERT INTO public.hr_grants(client_id,user_id,action,scope,fields)
 VALUES(p_client,p_new_owner,'directory.read','hr_operator',
  ARRAY['assigned_manager','department','display_name','job_title','work_email'])
 ON CONFLICT(client_id,user_id,action,scope) DO UPDATE SET fields=EXCLUDED.fields;
 INSERT INTO public.hr_workflow_grants(client_id,user_id,domain,action,scope)
 SELECT p_client,p_new_owner,d,a,'client_owner'
  FROM unnest(ARRAY['recruitment','attendance','leave']) AS d
  CROSS JOIN unnest(ARRAY['read','prepare','decide']) AS a
 ON CONFLICT DO NOTHING;
 -- hr_private_grants.approved_by must be a member of the company (0410). A platform admin who is
 -- not one can't be named there, so the new owner is; hr_client_owners, hr_authority_events and
 -- console_audit below name the real actor.
 INSERT INTO public.hr_private_grants(client_id,user_id,area,approved_by)
 SELECT p_client,p_new_owner,area,
  CASE WHEN EXISTS(SELECT 1 FROM public.console_memberships m
                   WHERE m.user_id=p_actor AND m.client_id=p_client)
       THEN p_actor ELSE p_new_owner END
  FROM unnest(ARRAY['recruitment','attendance']) AS area
 ON CONFLICT DO NOTHING;

 -- subject_id is the new owner, always a member, so a platform admin can act. The same key already
 -- spent by another operation of this person collides here, and is the same conflict.
 BEGIN
  INSERT INTO public.hr_operations(client_id,subject_id,request_key,operation,fingerprint,receipt)
  VALUES(p_client,p_new_owner,p_request_key,'owner.move',p_fingerprint,
   jsonb_build_object('old_owner',holder,'new_owner',p_new_owner));
 EXCEPTION WHEN unique_violation THEN
  RAISE EXCEPTION 'hr_idempotency_conflict' USING ERRCODE='23514';
 END;
 INSERT INTO public.hr_authority_events(client_id,subject_id,request_key,operation,details)
 VALUES(p_client,p_new_owner,p_request_key,'owner.move',
  jsonb_build_object('user_id',p_new_owner,'old_owner_id',holder,'actor_id',p_actor));
 INSERT INTO public.console_audit(actor_id,client_id,action,target_id,details)
 VALUES(p_actor,p_client,'hr.owner_moved',p_new_owner::text,
  jsonb_build_object('old_owner_id',holder,'new_owner_id',p_new_owner));

 RETURN QUERY SELECT holder, p_new_owner, false;
END $$;

-- Who the HR owner is, for the people who may move them, and nothing else about HR. usable is false
-- when the owner's account or membership is disabled or their role is read_only.
CREATE FUNCTION public.hr_owner_read(p_client uuid, p_actor uuid)
RETURNS TABLE(user_id uuid, display_name text, usable boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS NULL OR p_actor IS NULL
  OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid
  OR p_actor IS DISTINCT FROM nullif(current_setting('app.hr_user_id',true),'')::uuid
  OR NOT EXISTS(SELECT 1 FROM public.console_users u
                LEFT JOIN public.console_memberships m ON m.user_id=u.id AND m.client_id=p_client
                WHERE u.id=p_actor AND u.is_enabled
                 AND (u.is_platform_admin OR (m.is_enabled AND m.role='tenant_admin'))) THEN
  RAISE EXCEPTION 'hr_owner_move_denied' USING ERRCODE='42501'; END IF;
 RETURN QUERY
 SELECT o.user_id, u.display_name, u.is_enabled AND m.is_enabled AND m.role<>'read_only'
 FROM public.hr_client_owners o
 JOIN public.console_users u ON u.id=o.user_id
 JOIN public.console_memberships m ON m.user_id=o.user_id AND m.client_id=o.client_id
 WHERE o.client_id=p_client;
END $$;

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.hr_owner_move(uuid,uuid,uuid,uuid,uuid,text)'::regprocedure,
  'public.hr_owner_read(uuid,uuid)'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;
