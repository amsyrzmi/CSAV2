-- Customer Inbox refinement, part 1a: the database keeps the queue state of a conversation (design
--   docs/superpowers/specs/2026-11-16-inbox-refinement-design.md, section 5.2).
-- What it adds:
--   public.inbox_queue_enter(p_client, p_conversation, p_reason, p_priority DEFAULT 'normal',
--     p_actor DEFAULT NULL, p_claim_for DEFAULT NULL) RETURNS boolean: the only code path that puts
--     a conversation in needs_staff. A no-op returning false for a conversation outside the queue
--     (channel other than telegram or whatsapp, the sandbox; a console chat thread or participant,
--     as db/sql/inbox/list_queue_*.sql excludes them) and for one the caller cannot see.
--     Not yet needs_staff: it becomes needs_staff with a fresh clock (needs_staff_since = now()),
--     the given reason and priority, snoozed_until and resolved_at cleared.
--     Already needs_staff: the clock is kept, the reason only moves up (escalation > held_reply >
--     manual > customer_message) and the priority only up (low < normal < high < urgent).
--     due_at = needs_staff_since + the company's response target for the resulting priority
--     (inbox_settings, else the 0621 column defaults), computed on entry and when the priority
--     rises; a change of reason or claim alone keeps the due time. p_claim_for claims an
--     unclaimed item. The reason and priority lists repeat the CHECK constraints of 0620 and
--     escalations_priority_check (tests/test_inbox_queue_triggers.py keeps them equal).
--     One inbox_queue_events row per change: entered, reason_changed (reason or priority moved), or
--     claimed (only the claim moved); its client_id and conversation_id come from the conversation
--     row itself. When nothing would change, nothing is written and it returns false.
--     It never changes inbox_version: that is the staff UI's concurrency counter, and a bump here
--     would make a person's next action on the conversation fail with 409.
--   Trigger inbox_queue_after_message, AFTER INSERT ON messages: last_activity_at for every message
--     (never moving it backwards); for a customer's message (inbound, author 'contact') also
--     last_customer_at and the inbound transition: an item waiting on the customer, snoozed or
--     resolved returns to needs_staff (reason customer_message, its current priority) when a person
--     owns the conversation or a live held reply is pending, else to bot (claim, clock, due time,
--     reason, snooze and resolve times cleared; one inbox_queue_events row 'handed_back' with no
--     actor and details {"cause": "customer_message"}). A bot item enters needs_staff the same way
--     when a person owns the conversation (the bot is silent, so the message needs a person); a
--     bot item the AI owns stays bot. needs_staff items keep their state.
--     Outbound messages (bot replies, notices, a person's reply, an approved held reply) never
--     change the queue state: the code that knows who answered does that (plan task 4).
--   Trigger inbox_queue_after_escalation, AFTER INSERT ON escalations WHEN conversation_id IS NOT
--     NULL: inbox_queue_enter(..., 'escalation', the escalation's priority). It covers the three
--     writers (the escalation agent, the turn fail-safe, approval expiry); an insert skipped by their
--     ON CONFLICT DO NOTHING fires nothing.
-- Cost and locks: a message costs one UPDATE of its conversation row (the return to bot is part of
--   that same statement); only an entry into needs_staff adds the inbox_queue_enter call. A
--   customer's message first reads the row with SELECT ... FOR NO KEY UPDATE, the lock the UPDATE
--   takes anyway, so the old state the event row needs is exact and no retry is needed. An
--   outbound message runs the UPDATE alone. The trigger never takes a lock the UPDATE would not
--   take: the worker and the console lock or update that row in the same transaction already
--   (lock_conversation.sql, automation_allowed.sql, increment_turn_count.sql). The functions run
--   with the caller's rights (no SECURITY DEFINER): row security keeps a company's trigger and
--   calls inside its own rows.
-- Pre-check: 0620 (queue columns) and 0621 (inbox_settings, inbox_queue_events) applied.
-- Safe to run twice: CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS then CREATE TRIGGER, grants
--   revoked and given again.
-- Rollback: DROP TRIGGER inbox_queue_after_message ON public.messages; DROP TRIGGER
--   inbox_queue_after_escalation ON public.escalations; DROP FUNCTION
--   public.inbox_queue_on_message(), public.inbox_queue_on_escalation(),
--   public.inbox_queue_enter(uuid, uuid, text, text, uuid, uuid); then delete the
--   schema_migrations row for 0622, or the runner thinks it is still applied.

CREATE OR REPLACE FUNCTION public.inbox_queue_enter(
    p_client uuid,
    p_conversation uuid,
    p_reason text,
    p_priority text DEFAULT 'normal',
    p_actor uuid DEFAULT NULL,
    p_claim_for uuid DEFAULT NULL)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
  -- Lowest first: array_position gives the rank.
  reasons constant text[] := ARRAY['customer_message', 'manual', 'held_reply', 'escalation'];
  priorities constant text[] := ARRAY['low', 'normal', 'high', 'urgent'];
  cur record;
  v_reason text;
  v_priority text;
  v_since timestamptz;
  v_due timestamptz;
  v_target integer;
  v_assignee uuid;
  v_claimed_at timestamptz;
  v_action text;
  v_details jsonb;
BEGIN
  IF p_reason IS NULL OR array_position(reasons, p_reason) IS NULL THEN
    RAISE EXCEPTION 'inbox_queue_enter: unknown reason %', coalesce(p_reason, 'NULL')
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_priority IS NULL OR array_position(priorities, p_priority) IS NULL THEN
    RAISE EXCEPTION 'inbox_queue_enter: unknown priority %', coalesce(p_priority, 'NULL')
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  SELECT c.queue_status, c.queue_reason, c.priority, c.needs_staff_since, c.due_at,
         c.assignee_user_id, c.claimed_at
    INTO cur
    FROM public.conversations c
   WHERE c.client_id = p_client AND c.id = p_conversation
     AND c.channel IN ('telegram', 'whatsapp')
     AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
     AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p
                      WHERE p.contact_id = c.contact_id)
     FOR NO KEY UPDATE OF c;
  IF NOT FOUND THEN
    RETURN false;
  END IF;

  IF cur.queue_status = 'needs_staff' THEN
    v_since := cur.needs_staff_since;
    v_reason := CASE WHEN array_position(reasons, p_reason)
                          > coalesce(array_position(reasons, cur.queue_reason), 0)
                     THEN p_reason ELSE cur.queue_reason END;
    v_priority := CASE WHEN array_position(priorities, p_priority)
                            > array_position(priorities, cur.priority)
                       THEN p_priority ELSE cur.priority END;
  ELSE
    v_since := now();
    v_reason := p_reason;
    v_priority := p_priority;
  END IF;
  v_assignee := coalesce(cur.assignee_user_id, p_claim_for);
  v_claimed_at := CASE WHEN cur.assignee_user_id IS NULL AND p_claim_for IS NOT NULL
                       THEN now() ELSE cur.claimed_at END;

  IF cur.queue_status <> 'needs_staff' THEN
    v_action := 'entered';
  ELSIF v_reason IS DISTINCT FROM cur.queue_reason OR v_priority <> cur.priority THEN
    v_action := 'reason_changed';
  ELSIF v_assignee IS DISTINCT FROM cur.assignee_user_id THEN
    v_action := 'claimed';
  ELSE
    RETURN false;
  END IF;

  IF cur.queue_status = 'needs_staff' AND v_priority = cur.priority THEN
    -- Only the reason or the claim moved: the due time stays.
    v_due := cur.due_at;
  ELSE
    -- The company's response target for the priority. Without a settings row the defaults are
    -- the 0621 column defaults (target_urgent_min 15, target_high_min 60, target_normal_min 240,
    -- target_low_min 1440); tests/test_inbox_queue_triggers.py checks the two stay equal.
    SELECT CASE v_priority WHEN 'urgent' THEN s.target_urgent_min
                           WHEN 'high' THEN s.target_high_min
                           WHEN 'normal' THEN s.target_normal_min
                           ELSE s.target_low_min END
      INTO v_target
      FROM public.inbox_settings s
     WHERE s.client_id = p_client;
    IF NOT FOUND THEN
      v_target := CASE v_priority WHEN 'urgent' THEN 15 WHEN 'high' THEN 60
                                  WHEN 'normal' THEN 240 ELSE 1440 END;
    END IF;
    v_due := v_since + v_target * interval '1 minute';
  END IF;

  -- Ids and enums only (design: details never hold names or text).
  v_details := jsonb_build_object('reason', v_reason, 'priority', v_priority);
  IF cur.queue_reason IS NOT NULL AND cur.queue_reason IS DISTINCT FROM v_reason THEN
    v_details := v_details || jsonb_build_object('previous_reason', cur.queue_reason);
  END IF;
  IF cur.priority IS DISTINCT FROM v_priority THEN
    v_details := v_details || jsonb_build_object('previous_priority', cur.priority);
  END IF;
  IF v_assignee IS DISTINCT FROM cur.assignee_user_id THEN
    v_details := v_details || jsonb_build_object('assignee_user_id', v_assignee);
  END IF;

  WITH moved AS (
    UPDATE public.conversations c
       SET queue_status = 'needs_staff',
           queue_reason = v_reason,
           priority = v_priority,
           needs_staff_since = v_since,
           due_at = v_due,
           assignee_user_id = v_assignee,
           claimed_at = v_claimed_at,
           snoozed_until = NULL,
           resolved_at = NULL
     WHERE c.client_id = p_client AND c.id = p_conversation
    RETURNING c.client_id, c.id
  )
  INSERT INTO public.inbox_queue_events
      (client_id, conversation_id, actor_user_id, action, from_status, to_status, details)
  SELECT m.client_id, m.id, p_actor, v_action, cur.queue_status, 'needs_staff', v_details
    FROM moved m;
  RETURN true;
END $$;

CREATE OR REPLACE FUNCTION public.inbox_queue_on_message()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  v_client uuid;
  v_id uuid;
  v_old text;
  v_owned text;
  v_priority text;
  v_in_queue boolean;
  v_to_bot boolean := false;
  v_enter boolean := false;
BEGIN
  IF NEW.direction <> 'inbound' OR NEW.author <> 'contact' THEN
    UPDATE public.conversations c
       SET last_activity_at = greatest(c.last_activity_at, NEW.created_at)
     WHERE c.client_id = NEW.client_id AND c.id = NEW.conversation_id;
    RETURN NULL;
  END IF;

  -- Lock first: the UPDATE below takes this row lock anyway, and holding it from the read on makes
  -- the old state (which decides the transition and goes into the event row) exact.
  SELECT c.client_id, c.id, c.queue_status, c.owned_by, c.priority,
         c.channel IN ('telegram', 'whatsapp')
           AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
           AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p
                            WHERE p.contact_id = c.contact_id)
    INTO v_client, v_id, v_old, v_owned, v_priority, v_in_queue
    FROM public.conversations c
   WHERE c.client_id = NEW.client_id AND c.id = NEW.conversation_id
     FOR NO KEY UPDATE OF c;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  IF v_in_queue THEN
    IF v_old = 'bot' THEN
      -- A person owns the conversation, so the bot stays silent: the message needs a person.
      v_enter := v_owned IS NOT DISTINCT FROM 'human_support';
    ELSIF v_old IN ('waiting_customer', 'snoozed', 'resolved') THEN
      v_enter := v_owned IS NOT DISTINCT FROM 'human_support'
                 OR EXISTS (
                   SELECT 1 FROM public.approvals a
                    WHERE a.client_id = v_client AND a.conversation_id = v_id
                      AND a.status = 'pending' AND a.expires_at > now());
      v_to_bot := NOT v_enter;
    END IF;
  END IF;

  -- One UPDATE: the times, and the return to bot of a waiting, snoozed or resolved item that no
  -- person owns and that has no live held reply.
  UPDATE public.conversations c
     SET last_activity_at = greatest(c.last_activity_at, NEW.created_at),
         last_customer_at = greatest(c.last_customer_at, NEW.created_at),
         queue_status = CASE WHEN v_to_bot THEN 'bot' ELSE c.queue_status END,
         queue_reason = CASE WHEN v_to_bot THEN NULL ELSE c.queue_reason END,
         assignee_user_id = CASE WHEN v_to_bot THEN NULL ELSE c.assignee_user_id END,
         claimed_at = CASE WHEN v_to_bot THEN NULL ELSE c.claimed_at END,
         needs_staff_since = CASE WHEN v_to_bot THEN NULL ELSE c.needs_staff_since END,
         due_at = CASE WHEN v_to_bot THEN NULL ELSE c.due_at END,
         snoozed_until = CASE WHEN v_to_bot THEN NULL ELSE c.snoozed_until END,
         resolved_at = CASE WHEN v_to_bot THEN NULL ELSE c.resolved_at END
   WHERE c.client_id = v_client AND c.id = v_id;

  IF v_to_bot THEN
    -- The bot has the conversation again: recorded like a hand back, by no person.
    INSERT INTO public.inbox_queue_events
        (client_id, conversation_id, actor_user_id, action, from_status, to_status, details)
    VALUES (v_client, v_id, NULL, 'handed_back', v_old, 'bot',
            jsonb_build_object('cause', 'customer_message'));
  ELSIF v_enter THEN
    PERFORM public.inbox_queue_enter(v_client, v_id, 'customer_message', v_priority);
  END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION public.inbox_queue_on_escalation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  PERFORM public.inbox_queue_enter(NEW.client_id, NEW.conversation_id, 'escalation', NEW.priority);
  RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS inbox_queue_after_message ON public.messages;
CREATE TRIGGER inbox_queue_after_message
    AFTER INSERT ON public.messages
    FOR EACH ROW EXECUTE FUNCTION public.inbox_queue_on_message();

DROP TRIGGER IF EXISTS inbox_queue_after_escalation ON public.escalations;
CREATE TRIGGER inbox_queue_after_escalation
    AFTER INSERT ON public.escalations
    FOR EACH ROW WHEN (NEW.conversation_id IS NOT NULL)
    EXECUTE FUNCTION public.inbox_queue_on_escalation();

-- inbox_queue_enter is called by the app (plan task 4) and by the triggers, which run with the
-- inserting role's rights. Trigger functions need no EXECUTE grant to fire.
REVOKE ALL ON FUNCTION public.inbox_queue_enter(uuid, uuid, text, text, uuid, uuid)
    FROM PUBLIC, csa_app, csa_ro, csa_identity;
GRANT EXECUTE ON FUNCTION public.inbox_queue_enter(uuid, uuid, text, text, uuid, uuid) TO csa_app;
REVOKE ALL ON FUNCTION public.inbox_queue_on_message(), public.inbox_queue_on_escalation()
    FROM PUBLIC, csa_app, csa_ro, csa_identity;
