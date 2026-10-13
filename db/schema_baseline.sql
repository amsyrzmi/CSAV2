--
-- PostgreSQL database dump
--

\restrict hhqIrgfYPj13adCheTdPTFAjE4DBVpUoT3OkuA6Kcm8yCtP0mwwfd9zZr19n559

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: postgis; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;


--
-- Name: EXTENSION postgis; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION postgis IS 'PostGIS geometry and geography spatial types and functions';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: contact_identity_tenant_check(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.contact_identity_tenant_check() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM contacts WHERE id = NEW.contact_id AND client_id = NEW.client_id) THEN
    RAISE EXCEPTION 'contact % does not belong to client %', NEW.contact_id, NEW.client_id;
  END IF;
  RETURN NEW;
END $$;


--
-- Name: csa_expire_drafts(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.csa_expire_drafts() RETURNS TABLE(expired_confirmations bigint, cancelled_orders bigint)
    LANGUAGE sql
    AS $$
WITH dead AS (
  UPDATE order_confirmations c SET state = 'expired', responded_at = now()
  WHERE c.state = 'pending' AND c.expires_at <= now()
  RETURNING c.client_id, c.order_id
),
upd AS (
  UPDATE orders o SET status = 'cancelled', cancelled_at = now(), updated_at = now()
  FROM dead d WHERE o.id = d.order_id AND o.status = 'draft'
  RETURNING o.id
),
rel AS (
  UPDATE products p SET reserved_qty = GREATEST(p.reserved_qty - oi.qty, 0), updated_at = now()
  FROM order_items oi, upd WHERE oi.order_id = upd.id AND p.id = oi.product_id
  RETURNING p.id
),
ev AS (
  INSERT INTO order_events (client_id, order_id, event, actor)
  SELECT d.client_id, u.id, 'expired', 'system' FROM upd u JOIN dead d ON d.order_id = u.id
  RETURNING order_id
)
SELECT (SELECT count(*) FROM dead), (SELECT count(*) FROM upd);
$$;


--
-- Name: csa_expire_pending_approvals(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.csa_expire_pending_approvals(p_hours integer DEFAULT 24) RETURNS TABLE(order_id text, total numeric, currency text, customer_telegram_id bigint)
    LANGUAGE sql
    AS $$
WITH stale AS (
  SELECT o.id, o.client_id, o.total, o.currency, o.contact_id
  FROM orders o
  WHERE o.status = 'pending_confirmation'
    AND COALESCE(o.placed_at, o.updated_at) <= now() - make_interval(hours => p_hours)
  FOR UPDATE OF o SKIP LOCKED
),
upd AS (
  UPDATE orders o SET status = 'cancelled', cancelled_at = now(), updated_at = now()
  FROM stale s WHERE o.id = s.id
  RETURNING o.id
),
rel AS (
  UPDATE products p SET reserved_qty = GREATEST(p.reserved_qty - oi.qty, 0), updated_at = now()
  FROM order_items oi, upd WHERE oi.order_id = upd.id AND p.id = oi.product_id
  RETURNING p.id
),
ev AS (
  INSERT INTO order_events (client_id, order_id, event, actor, payload)
  SELECT s.client_id, s.id, 'cancelled', 'system',
         jsonb_build_object('reason', 'approval_timeout', 'hours', p_hours)
  FROM stale s JOIN upd u ON u.id = s.id
  RETURNING order_id
)
SELECT s.id, s.total, s.currency, ct.telegram_id
FROM stale s JOIN upd u ON u.id = s.id
LEFT JOIN contacts ct ON ct.id = s.contact_id;
$$;


--
-- Name: csa_retention(boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.csa_retention(p_dry_run boolean DEFAULT true) RETURNS TABLE(target text, affected bigint, note text)
    LANGUAGE plpgsql
    AS $$
DECLARE
  pol record;
  n bigint;
BEGIN
  FOR pol IN
    SELECT * FROM (VALUES
      ('outbound_delivery_attempts', 'created_at', '30 days', 'true'),
      ('outbound_deliveries',        'created_at', '30 days', 'true'),
      ('voice_transcripts',          'created_at', '30 days', 'true'),
      ('ingress_events',             'created_at', '30 days', 'true'),
      ('ingress_dedup',              'created_at', '7 days',  'true'),
      ('agent_runs',                 'created_at', '30 days', 'true'),
      ('action_log',                 'created_at', '90 days', 'true'),
      ('dlq',                        'created_at', '90 days', 'true'),
      ('order_confirmations',        'created_at', '30 days', 'state <> ''pending'''),
      ('pending_actions',            'created_at', '7 days',  'status <> ''open'' OR expires_at < now()')
    ) AS v(tbl, col, keep, extra)
  LOOP
    target := pol.tbl;
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
        WHERE c.table_schema = 'public' AND c.table_name = pol.tbl AND c.column_name = pol.col
      ) THEN
        affected := 0; note := 'skipped: column ' || pol.col || ' missing';
        RETURN NEXT; CONTINUE;
      END IF;

      IF p_dry_run THEN
        EXECUTE format('SELECT count(*) FROM %I WHERE %I < now() - %L::interval AND (%s)',
                       pol.tbl, pol.col, pol.keep, pol.extra) INTO n;
        note := 'dry run';
      ELSE
        EXECUTE format('WITH d AS (DELETE FROM %I WHERE %I < now() - %L::interval AND (%s) RETURNING 1) SELECT count(*) FROM d',
                       pol.tbl, pol.col, pol.keep, pol.extra) INTO n;
        note := 'deleted';
      END IF;
      affected := n; RETURN NEXT;
    EXCEPTION WHEN OTHERS THEN
      affected := 0; note := 'error: ' || SQLERRM;
      RETURN NEXT;
    END;
  END LOOP;

  -- messages: keep history, drop raw PII after 30 days
  target := 'messages (scrub raw body)';
  BEGIN
    IF p_dry_run THEN
      SELECT count(*) INTO n FROM messages
      WHERE redacted_body IS NOT NULL AND body IS DISTINCT FROM redacted_body
        AND created_at < now() - interval '30 days';
      note := 'dry run';
    ELSE
      UPDATE messages SET body = redacted_body
      WHERE redacted_body IS NOT NULL AND body IS DISTINCT FROM redacted_body
        AND created_at < now() - interval '30 days';
      GET DIAGNOSTICS n = ROW_COUNT;
      note := 'scrubbed';
    END IF;
    affected := n; RETURN NEXT;
  EXCEPTION WHEN OTHERS THEN
    affected := 0; note := 'error: ' || SQLERRM;
    RETURN NEXT;
  END;
END;
$$;


--
-- Name: enqueue_webhook_delivery(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_webhook_delivery() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
  v_channel  text;
  v_thread   text;
  v_ep_id    uuid;
  v_evt_at   timestamptz;
  v_ep       webhook_endpoints%ROWTYPE;
  v_delay    interval := interval '0';
BEGIN
  SELECT channel, channel_thread INTO v_channel, v_thread
    FROM conversations WHERE id = NEW.conversation_id;
  IF v_channel IS DISTINCT FROM 'webhook' THEN
    RETURN NEW;
  END IF;

  IF NEW.trace_id IS NOT NULL THEN
    SELECT endpoint_id, created_at INTO v_ep_id, v_evt_at
      FROM ingress_events WHERE trace_id = NEW.trace_id;
  END IF;

  IF v_ep_id IS NULL THEN
    SELECT id INTO v_ep_id FROM webhook_endpoints
     WHERE client_id = NEW.client_id AND public_id = split_part(v_thread, ':', 1);
  END IF;
  IF v_ep_id IS NULL THEN RETURN NEW; END IF;

  SELECT * INTO v_ep FROM webhook_endpoints WHERE id = v_ep_id;
  IF v_ep.callback_url IS NULL THEN RETURN NEW; END IF;

  IF v_ep.reply_mode = 'sync' AND v_evt_at IS NOT NULL
     AND now() < v_evt_at + make_interval(secs => (v_ep.sync_wait_ms / 1000.0 + 2)::double precision) THEN
    v_delay := interval '10 seconds';
  END IF;

  INSERT INTO outbound_deliveries
    (client_id, endpoint_id, trace_id, conversation_id, message_id, next_attempt_at)
  VALUES
    (NEW.client_id, v_ep.id, NEW.trace_id, NEW.conversation_id, NEW.id, now() + v_delay)
  ON CONFLICT (message_id) DO NOTHING;

  RETURN NEW;
EXCEPTION WHEN OTHERS THEN
  RAISE WARNING 'enqueue_webhook_delivery failed for message %: %', NEW.id, SQLERRM;
  RETURN NEW;
END $$;


--
-- Name: keep_escalated_status(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.keep_escalated_status() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF OLD.status = 'escalated'
     AND NEW.status = 'open'
     AND OLD.owned_by = 'human_support'
     AND NEW.owned_by IS NOT DISTINCT FROM OLD.owned_by      -- a deliberate release changes owned_by
     AND (OLD.ai_muted_until IS NULL OR OLD.ai_muted_until > now())
  THEN
    NEW.status := OLD.status;
  END IF;
  RETURN NEW;
END $$;


--
-- Name: purge_ingress_raw(interval); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.purge_ingress_raw(retain interval DEFAULT '7 days'::interval) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE n integer;
BEGIN
  UPDATE ingress_events
     SET raw = NULL, raw_purged_at = now()
   WHERE raw IS NOT NULL
     AND status IN ('completed','failed','dead')
     AND created_at < now() - retain;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;


--
-- Name: sync_order_items_json(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sync_order_items_json() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  target_order text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    target_order := OLD.order_id;
  ELSE
    target_order := NEW.order_id;
  END IF;

  UPDATE orders o SET
    items = COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
               'sku', oi.sku, 'name', oi.name_snapshot,
               'qty', oi.qty, 'price', oi.unit_price) ORDER BY oi.id)
      FROM order_items oi WHERE oi.order_id = target_order), '[]'::jsonb),
    subtotal = COALESCE((SELECT SUM(line_total) FROM order_items WHERE order_id = target_order), 0),
    updated_at = now()
  WHERE o.id = target_order;

  RETURN NULL;
END;
$$;


--
-- Name: tickets_touch_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.tickets_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$;


--
-- Name: webhook_set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.webhook_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: action_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_log (
    id bigint NOT NULL,
    client_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    action_type text NOT NULL,
    idempotency_key text NOT NULL,
    payload jsonb,
    result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: action_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.action_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: action_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.action_log_id_seq OWNED BY public.action_log.id;


--
-- Name: agent_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_runs (
    id bigint NOT NULL,
    client_id uuid NOT NULL,
    trace_id uuid NOT NULL,
    conversation_id uuid,
    agent text NOT NULL,
    task_id text,
    ok boolean NOT NULL,
    status text DEFAULT 'ok'::text NOT NULL,
    error_code text,
    error_category text,
    latency_ms integer,
    model text,
    prompt_tokens integer,
    completion_tokens integer,
    input jsonb,
    output jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_runs_id_seq OWNED BY public.agent_runs.id;


--
-- Name: approval_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.approval_audit (
    id bigint NOT NULL,
    client_id uuid NOT NULL,
    approval_id bigint NOT NULL,
    action text NOT NULL,
    actor text NOT NULL,
    old_values jsonb,
    new_values jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: approval_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.approval_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: approval_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.approval_audit_id_seq OWNED BY public.approval_audit.id;


--
-- Name: approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.approvals (
    id bigint NOT NULL,
    client_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    trace_id uuid NOT NULL,
    draft text NOT NULL,
    reason text NOT NULL,
    risk_score numeric(3,2),
    status text DEFAULT 'pending'::text NOT NULL,
    decided_by text,
    decided_at timestamp with time zone,
    final_text text,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: approvals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.approvals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: approvals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.approvals_id_seq OWNED BY public.approvals.id;


--
-- Name: bookings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bookings (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    contact_id uuid,
    service text NOT NULL,
    resource text,
    status text DEFAULT 'confirmed'::text NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    cancelled_at timestamp with time zone,
    cancel_reason text,
    calendar_event_id text
);


--
-- Name: branches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.branches (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    name text NOT NULL,
    address text NOT NULL,
    phone text,
    hours jsonb NOT NULL,
    location point,
    is_active boolean DEFAULT true NOT NULL,
    amenities jsonb DEFAULT '[]'::jsonb NOT NULL,
    store_type text,
    parking_info jsonb DEFAULT '{}'::jsonb NOT NULL,
    holiday_exceptions jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: capability_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.capability_registry (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    name text NOT NULL,
    display_name text NOT NULL,
    description text NOT NULL,
    pattern text NOT NULL,
    tier text DEFAULT 'core'::text NOT NULL,
    version text DEFAULT '1.0.0'::text NOT NULL,
    risk_class text DEFAULT 'low'::text NOT NULL,
    workflow_id text NOT NULL,
    input_schema jsonb NOT NULL,
    config_schema jsonb,
    timeout_ms integer DEFAULT 20000 NOT NULL,
    max_retries integer DEFAULT 1 NOT NULL,
    max_tool_calls integer DEFAULT 5 NOT NULL,
    max_iterations integer DEFAULT 3 NOT NULL,
    requires_human boolean DEFAULT false NOT NULL,
    is_enabled boolean DEFAULT true NOT NULL,
    circuit_state text DEFAULT 'closed'::text NOT NULL,
    fail_count integer DEFAULT 0 NOT NULL,
    opened_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    capability_kind text DEFAULT 'dispatched'::text NOT NULL,
    read_scopes jsonb DEFAULT '[]'::jsonb NOT NULL,
    write_scopes jsonb DEFAULT '[]'::jsonb NOT NULL,
    authorization_mode text DEFAULT 'contact'::text NOT NULL,
    client_enabled boolean DEFAULT true NOT NULL,
    client_enabled_changed_at timestamp with time zone,
    client_enabled_changed_by text,
    CONSTRAINT capability_registry_capability_kind_check CHECK ((capability_kind = ANY (ARRAY['dispatched'::text, 'ingress'::text])))
);


--
-- Name: COLUMN capability_registry.client_enabled; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.capability_registry.client_enabled IS 'Switch owned by the client. An agent runs only when both client_enabled and is_enabled are true.';


--
-- Name: channel_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.channel_credentials (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    channel text NOT NULL,
    shop_id text,
    access_token text,
    refresh_token text,
    expires_at timestamp with time zone,
    meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: clients; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clients (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    tier text DEFAULT 'growth'::text NOT NULL,
    status text DEFAULT 'onboarding'::text NOT NULL,
    platform_version text NOT NULL,
    timezone text DEFAULT 'Asia/Kuala_Lumpur'::text NOT NULL,
    region text DEFAULT 'MY'::text NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    brand_name text,
    brand_tone text,
    languages text[] DEFAULT '{en}'::text[] NOT NULL,
    core_object text DEFAULT 'order'::text NOT NULL,
    deployment_mode text DEFAULT 'shadow'::text NOT NULL,
    tokens_per_day integer DEFAULT 400000 NOT NULL,
    onboarded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    email text
);


--
-- Name: contact_identities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contact_identities (
    client_id uuid NOT NULL,
    channel text NOT NULL,
    external_id text NOT NULL,
    contact_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT contact_identities_external_id_check CHECK (((length(external_id) >= 1) AND (length(external_id) <= 200)))
);


--
-- Name: contact_verifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contact_verifications (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    contact_id uuid NOT NULL,
    channel text NOT NULL,
    code_hash text NOT NULL,
    pending_field text NOT NULL,
    pending_value text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    verified_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: contacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contacts (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    email text,
    telegram_id bigint,
    phone text,
    full_name text,
    tier text DEFAULT 'standard'::text NOT NULL,
    locale text DEFAULT 'en'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    address text,
    console_user_id uuid,
    contact_kind text DEFAULT 'customer'::text NOT NULL
);


--
-- Name: conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversations (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    contact_id uuid NOT NULL,
    channel text NOT NULL,
    channel_thread text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    summary text,
    turn_count integer DEFAULT 0 NOT NULL,
    clarify_count integer DEFAULT 0 NOT NULL,
    sentiment text,
    last_intent text,
    ai_muted_until timestamp with time zone,
    owned_by text,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone
);


--
-- Name: dlq; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dlq (
    id bigint NOT NULL,
    client_id uuid NOT NULL,
    trace_id uuid,
    stage text NOT NULL,
    envelope jsonb NOT NULL,
    error jsonb NOT NULL,
    replay_attempts integer DEFAULT 0 NOT NULL,
    max_replays integer DEFAULT 2 NOT NULL,
    replayed boolean DEFAULT false NOT NULL,
    abandoned boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dlq_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dlq_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dlq_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dlq_id_seq OWNED BY public.dlq.id;


--
-- Name: ingress_dedup; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ingress_dedup (
    client_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    trace_id uuid NOT NULL,
    channel text NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ingress_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ingress_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    client_id uuid NOT NULL,
    endpoint_id uuid NOT NULL,
    contact_id uuid,
    idempotency_key text NOT NULL,
    payload_hash text NOT NULL,
    trace_id uuid NOT NULL,
    raw jsonb,
    status text DEFAULT 'received'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    dispatched_at timestamp with time zone,
    completed_at timestamp with time zone,
    raw_purged_at timestamp with time zone,
    CONSTRAINT ingress_events_status_check CHECK ((status = ANY (ARRAY['received'::text, 'dispatched'::text, 'completed'::text, 'failed'::text, 'dead'::text])))
);


--
-- Name: invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoices (
    id text NOT NULL,
    client_id uuid NOT NULL,
    contact_id uuid,
    order_id text,
    status text NOT NULL,
    amount numeric(12,2) NOT NULL,
    currency text DEFAULT 'MYR'::text NOT NULL,
    issued_at timestamp with time zone NOT NULL,
    due_at timestamp with time zone,
    paid_at timestamp with time zone,
    payment_ref text,
    line_items jsonb DEFAULT '[]'::jsonb NOT NULL
);


--
-- Name: kb_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kb_chunks (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    document_id uuid NOT NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    heading text,
    embedding public.vector(384) NOT NULL,
    token_count integer,
    question text
);


--
-- Name: kb_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kb_documents (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    title text NOT NULL,
    source_uri text,
    category text,
    language text DEFAULT 'en'::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    file_name text,
    content_hash text,
    chunk_count integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    kind text DEFAULT 'doc'::text NOT NULL,
    embed_model text,
    source_id uuid,
    CONSTRAINT kb_documents_kind_check CHECK ((kind = ANY (ARRAY['faq'::text, 'doc'::text])))
);


--
-- Name: kb_gaps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kb_gaps (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    query text NOT NULL,
    query_hash text GENERATED ALWAYS AS (md5(lower(query))) STORED,
    hits integer DEFAULT 1 NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    resolved boolean DEFAULT false NOT NULL
);


--
-- Name: kb_ingest_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kb_ingest_jobs (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    source text NOT NULL,
    source_id uuid,
    status text DEFAULT 'queued'::text NOT NULL,
    files jsonb DEFAULT '[]'::jsonb NOT NULL,
    meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    requested_by text,
    attempts integer DEFAULT 0 NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    locked_at timestamp with time zone,
    finished_at timestamp with time zone,
    CONSTRAINT kb_ingest_jobs_source_check CHECK ((source = ANY (ARRAY['upload'::text, 'sheet'::text]))),
    CONSTRAINT kb_ingest_jobs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'processing'::text, 'done'::text, 'partial'::text, 'failed'::text])))
);


--
-- Name: kb_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kb_sources (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    kind text DEFAULT 'gsheet'::text NOT NULL,
    sheet_id text NOT NULL,
    gid text DEFAULT '0'::text NOT NULL,
    url text NOT NULL,
    title text NOT NULL,
    category text,
    language text DEFAULT 'en'::text NOT NULL,
    interval_hours integer DEFAULT 24 NOT NULL,
    active boolean DEFAULT true NOT NULL,
    last_synced timestamp with time zone,
    last_status text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT kb_sources_interval_hours_check CHECK (((interval_hours >= 0) AND (interval_hours <= 168))),
    CONSTRAINT kb_sources_kind_check CHECK ((kind = 'gsheet'::text))
);


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    direction text NOT NULL,
    author text NOT NULL,
    body text NOT NULL,
    redacted_body text,
    attachments jsonb DEFAULT '[]'::jsonb NOT NULL,
    has_attachment boolean DEFAULT false NOT NULL,
    idempotency_key text NOT NULL,
    trace_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: order_confirmations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.order_confirmations (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    order_id text NOT NULL,
    contact_id uuid NOT NULL,
    token text NOT NULL,
    channel text DEFAULT 'telegram'::text NOT NULL,
    state text DEFAULT 'pending'::text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    responded_at timestamp with time zone,
    response_meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT order_confirmations_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'confirmed'::text, 'cancelled'::text, 'expired'::text])))
);


--
-- Name: order_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.order_events (
    id bigint NOT NULL,
    client_id uuid NOT NULL,
    order_id text NOT NULL,
    event text NOT NULL,
    actor text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: order_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.order_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: order_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.order_events_id_seq OWNED BY public.order_events.id;


--
-- Name: order_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.order_items (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    order_id text NOT NULL,
    product_id uuid,
    sku text NOT NULL,
    name_snapshot text NOT NULL,
    unit_price numeric(12,2) NOT NULL,
    qty integer NOT NULL,
    line_total numeric(12,2) GENERATED ALWAYS AS ((unit_price * (qty)::numeric)) STORED,
    currency text DEFAULT 'MYR'::text NOT NULL,
    external_line_id text,
    CONSTRAINT order_items_qty_check CHECK ((qty > 0))
);


--
-- Name: orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.orders (
    id text NOT NULL,
    client_id uuid NOT NULL,
    contact_id uuid,
    status text NOT NULL,
    total numeric(12,2) NOT NULL,
    currency text DEFAULT 'MYR'::text NOT NULL,
    placed_at timestamp with time zone,
    shipped_at timestamp with time zone,
    tracking_no text,
    carrier text,
    items jsonb DEFAULT '[]'::jsonb NOT NULL,
    channel text DEFAULT 'telegram'::text NOT NULL,
    external_order_id text,
    external_shop_id text,
    payment_status text DEFAULT 'unpaid'::text NOT NULL,
    subtotal numeric(12,2),
    shipping_fee numeric(12,2) DEFAULT 0 NOT NULL,
    discount numeric(12,2) DEFAULT 0 NOT NULL,
    ship_to jsonb,
    idempotency_key text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    confirmed_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    CONSTRAINT orders_channel_chk CHECK ((channel = ANY (ARRAY['telegram'::text, 'web'::text, 'tiktok_shop'::text, 'shopee'::text, 'manual'::text]))),
    CONSTRAINT orders_payment_chk CHECK ((payment_status = ANY (ARRAY['unpaid'::text, 'cod_pending'::text, 'paid'::text, 'refunded'::text, 'failed'::text]))),
    CONSTRAINT orders_status_chk CHECK ((status = ANY (ARRAY['draft'::text, 'pending_confirmation'::text, 'confirmed'::text, 'processing'::text, 'shipped'::text, 'delivered'::text, 'cancelled'::text, 'refunded'::text])))
);


--
-- Name: outbound_deliveries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbound_deliveries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    client_id uuid NOT NULL,
    endpoint_id uuid NOT NULL,
    trace_id uuid,
    conversation_id uuid,
    message_id uuid NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    locked_at timestamp with time zone,
    last_status_code integer,
    last_error text,
    delivered_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT outbound_deliveries_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'delivering'::text, 'delivered'::text, 'skipped'::text, 'dead'::text])))
);


--
-- Name: outbound_delivery_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbound_delivery_attempts (
    id bigint NOT NULL,
    delivery_id uuid NOT NULL,
    attempt_no integer NOT NULL,
    status_code integer,
    error text,
    duration_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: outbound_delivery_attempts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.outbound_delivery_attempts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: outbound_delivery_attempts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.outbound_delivery_attempts_id_seq OWNED BY public.outbound_delivery_attempts.id;


--
-- Name: pending_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pending_actions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    contact_id uuid NOT NULL,
    agent text NOT NULL,
    action text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plans (
    client_id uuid NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    price numeric(12,2) NOT NULL,
    currency text DEFAULT 'MYR'::text NOT NULL,
    billing_interval text DEFAULT 'month'::text NOT NULL,
    features jsonb DEFAULT '[]'::jsonb NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT plans_code_check CHECK ((code = lower(code)))
);


--
-- Name: products; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.products (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    sku text NOT NULL,
    name text NOT NULL,
    description text,
    category text,
    brand text,
    price numeric(12,2) NOT NULL,
    compare_at_price numeric(12,2),
    currency text DEFAULT 'MYR'::text NOT NULL,
    unit text DEFAULT 'unit'::text NOT NULL,
    stock_qty integer DEFAULT 0 NOT NULL,
    reserved_qty integer DEFAULT 0 NOT NULL,
    available_qty integer GENERATED ALWAYS AS (GREATEST((stock_qty - reserved_qty), 0)) STORED,
    weight_grams integer,
    image_url text,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT products_price_check CHECK ((price >= (0)::numeric)),
    CONSTRAINT products_reserved_qty_check CHECK ((reserved_qty >= 0)),
    CONSTRAINT products_stock_qty_check CHECK ((stock_qty >= 0))
);


--
-- Name: price_book; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.price_book AS
 SELECT id,
    client_id,
    sku,
    name,
    price,
    currency,
    unit,
    is_active
   FROM public.products;


--
-- Name: price_book_legacy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_book_legacy (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    sku text NOT NULL,
    name text NOT NULL,
    price numeric(12,2) NOT NULL,
    currency text DEFAULT 'MYR'::text NOT NULL,
    unit text DEFAULT 'unit'::text NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: product_channel_map; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.product_channel_map (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    product_id uuid NOT NULL,
    channel text NOT NULL,
    external_product_id text NOT NULL,
    external_sku_id text,
    external_shop_id text,
    sync_status text DEFAULT 'pending'::text NOT NULL,
    last_synced_at timestamp with time zone,
    raw jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT product_channel_map_channel_check CHECK ((channel = ANY (ARRAY['tiktok_shop'::text, 'shopee'::text, 'telegram'::text, 'web'::text])))
);


--
-- Name: quote_catalog; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.quote_catalog AS
 SELECT DISTINCT ON (client_id, sku) id,
    client_id,
    sku,
    name,
    price,
    currency,
    unit
   FROM public.price_book
  WHERE ((is_active = true) AND (sku IS NOT NULL) AND (price IS NOT NULL))
  ORDER BY client_id, sku, id;


--
-- Name: quotes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quotes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    quote_no bigint NOT NULL,
    client_id uuid NOT NULL,
    contact_id uuid,
    conversation_id uuid,
    trace_id uuid,
    currency text NOT NULL,
    subtotal numeric(12,2) NOT NULL,
    tax numeric(12,2) DEFAULT 0 NOT NULL,
    total numeric(12,2) NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    valid_until date,
    lines jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT quotes_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'pending_approval'::text, 'sent'::text, 'accepted'::text, 'expired'::text, 'cancelled'::text])))
);


--
-- Name: quotes_quote_no_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.quotes_quote_no_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: quotes_quote_no_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.quotes_quote_no_seq OWNED BY public.quotes.quote_no;


--
-- Name: refunds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.refunds (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    order_id text NOT NULL,
    status text DEFAULT 'requested'::text NOT NULL,
    amount numeric(12,2) NOT NULL,
    currency text DEFAULT 'MYR'::text NOT NULL,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    processed_at timestamp with time zone
);


--
-- Name: subscription_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscription_events (
    id bigint NOT NULL,
    client_id uuid NOT NULL,
    subscription_id uuid NOT NULL,
    actor text NOT NULL,
    action text NOT NULL,
    from_status text,
    to_status text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    trace_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: subscription_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.subscription_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: subscription_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.subscription_events_id_seq OWNED BY public.subscription_events.id;


--
-- Name: subscription_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscription_requests (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    subscription_id uuid NOT NULL,
    contact_id uuid,
    conversation_id uuid,
    type text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    trace_id uuid,
    decided_by text,
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscriptions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    contact_id uuid,
    plan text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    price numeric(12,2) NOT NULL,
    currency text DEFAULT 'MYR'::text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    renews_at timestamp with time zone,
    paused_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    resume_at date,
    pending_plan text,
    pending_effective_at timestamp with time zone,
    cancel_at_period_end boolean DEFAULT false NOT NULL
);


--
-- Name: ticket_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ticket_events (
    id bigint NOT NULL,
    client_id uuid NOT NULL,
    ticket_id bigint NOT NULL,
    actor text NOT NULL,
    event_type text NOT NULL,
    old_value jsonb,
    new_value jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ticket_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ticket_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ticket_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ticket_events_id_seq OWNED BY public.ticket_events.id;


--
-- Name: tickets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tickets (
    id bigint NOT NULL,
    client_id uuid NOT NULL,
    conversation_id uuid,
    contact_id uuid,
    subject text NOT NULL,
    category text NOT NULL,
    priority text DEFAULT 'normal'::text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    assignee text,
    sla_due_at timestamp with time zone,
    resolution text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone
);


--
-- Name: tickets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tickets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tickets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tickets_id_seq OWNED BY public.tickets.id;


--
-- Name: v_booking_schedule; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_booking_schedule AS
 SELECT b.id,
    b.client_id,
    b.service,
    b.resource,
    b.status,
    b.starts_at,
    b.ends_at,
    c.email,
    c.telegram_id,
    c.full_name
   FROM (public.bookings b
     JOIN public.contacts c ON ((c.id = b.contact_id)));


--
-- Name: v_invoice_status; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_invoice_status AS
 SELECT i.id,
    i.client_id,
    i.status,
    i.amount,
    i.currency,
    i.issued_at,
    i.due_at,
    i.paid_at,
    c.email,
    c.telegram_id,
    c.tier
   FROM (public.invoices i
     JOIN public.contacts c ON ((c.id = i.contact_id)));


--
-- Name: v_order_status; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_order_status AS
 SELECT o.id,
    o.client_id,
    o.status,
    o.tracking_no,
    o.carrier,
    o.placed_at,
    o.shipped_at,
    o.total,
    o.currency,
    c.email,
    c.telegram_id,
    c.tier,
    o.items,
    o.channel,
    o.payment_status,
    o.contact_id,
    o.external_order_id
   FROM (public.orders o
     LEFT JOIN public.contacts c ON ((c.id = o.contact_id)));


--
-- Name: v_product_catalog; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_product_catalog AS
 SELECT client_id,
    sku,
    name,
    description,
    category,
    brand,
    price,
    currency,
    unit,
    available_qty,
    image_url,
    is_active
   FROM public.products p
  WHERE is_active;


--
-- Name: v_store_unserved_demand; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_store_unserved_demand AS
 SELECT client_id,
    COALESCE(NULLIF(btrim((input ->> 'location_query'::text)), ''::text), '(no area provided)'::text) AS search_area,
    count(*) AS failed_searches,
    min(created_at) AS first_seen_at,
    max(created_at) AS last_seen_at
   FROM public.agent_runs
  WHERE ((agent = 'store_agent'::text) AND (error_code = 'STORE_NOT_FOUND'::text))
  GROUP BY client_id, COALESCE(NULLIF(btrim((input ->> 'location_query'::text)), ''::text), '(no area provided)'::text);


--
-- Name: v_warranty_eligibility; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_warranty_eligibility AS
 SELECT o.id AS order_id,
    o.client_id,
    o.contact_id,
    (item.value ->> 'sku'::text) AS sku,
    (item.value ->> 'name'::text) AS product_name,
    o.placed_at,
    (o.placed_at + '1 year'::interval) AS warranty_expires_at,
    ((o.placed_at + '1 year'::interval) > now()) AS under_warranty
   FROM public.orders o,
    LATERAL jsonb_array_elements(o.items) item(value);


--
-- Name: voice_transcripts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.voice_transcripts (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    client_id uuid NOT NULL,
    message_id uuid,
    audio_uri text,
    transcript text,
    language text,
    duration_secs numeric,
    model text NOT NULL,
    status text DEFAULT 'processing'::text NOT NULL,
    error_code text,
    livekit_job_id text,
    room_name text,
    envelope jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    telegram_file_id text,
    confidence numeric,
    callback_attempts integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: warranty_claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.warranty_claims (
    id bigint NOT NULL,
    client_id uuid NOT NULL,
    contact_id uuid,
    order_id text,
    sku text NOT NULL,
    issue text NOT NULL,
    status text DEFAULT 'filed'::text NOT NULL,
    filed_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone
);


--
-- Name: warranty_claims_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.warranty_claims_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: warranty_claims_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.warranty_claims_id_seq OWNED BY public.warranty_claims.id;


--
-- Name: webhook_credentials; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.webhook_credentials WITH (security_barrier='true') AS
 SELECT id,
    client_id,
    channel,
    shop_id AS public_id,
    access_token AS secret_enc,
    refresh_token AS secret_prev_enc,
    expires_at AS prev_expires_at,
    meta,
    updated_at
   FROM public.channel_credentials
  WHERE (channel = 'webhook'::text)
  WITH CASCADED CHECK OPTION;


--
-- Name: webhook_endpoints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.webhook_endpoints (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    client_id uuid NOT NULL,
    public_id text NOT NULL,
    credential_id uuid NOT NULL,
    name text DEFAULT 'Default'::text NOT NULL,
    reply_mode text DEFAULT 'sync'::text NOT NULL,
    sync_wait_ms integer DEFAULT 20000 NOT NULL,
    callback_url text,
    rate_limit_rpm integer DEFAULT 120 NOT NULL,
    is_enabled boolean DEFAULT true NOT NULL,
    disabled_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT callback_mode_needs_url CHECK (((reply_mode <> 'callback'::text) OR (callback_url IS NOT NULL))),
    CONSTRAINT webhook_endpoints_callback_url_check CHECK (((callback_url IS NULL) OR (callback_url ~* '^https://[^[:space:]]+$'::text))),
    CONSTRAINT webhook_endpoints_public_id_check CHECK ((public_id ~ '^[A-Za-z0-9_-]{16,64}$'::text)),
    CONSTRAINT webhook_endpoints_rate_limit_rpm_check CHECK (((rate_limit_rpm >= 1) AND (rate_limit_rpm <= 6000))),
    CONSTRAINT webhook_endpoints_reply_mode_check CHECK ((reply_mode = ANY (ARRAY['sync'::text, 'callback'::text]))),
    CONSTRAINT webhook_endpoints_sync_wait_ms_check CHECK (((sync_wait_ms >= 1000) AND (sync_wait_ms <= 60000)))
);


--
-- Name: action_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_log ALTER COLUMN id SET DEFAULT nextval('public.action_log_id_seq'::regclass);


--
-- Name: agent_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs ALTER COLUMN id SET DEFAULT nextval('public.agent_runs_id_seq'::regclass);


--
-- Name: approval_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approval_audit ALTER COLUMN id SET DEFAULT nextval('public.approval_audit_id_seq'::regclass);


--
-- Name: approvals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals ALTER COLUMN id SET DEFAULT nextval('public.approvals_id_seq'::regclass);


--
-- Name: dlq id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dlq ALTER COLUMN id SET DEFAULT nextval('public.dlq_id_seq'::regclass);


--
-- Name: order_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_events ALTER COLUMN id SET DEFAULT nextval('public.order_events_id_seq'::regclass);


--
-- Name: outbound_delivery_attempts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_delivery_attempts ALTER COLUMN id SET DEFAULT nextval('public.outbound_delivery_attempts_id_seq'::regclass);


--
-- Name: quotes quote_no; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotes ALTER COLUMN quote_no SET DEFAULT nextval('public.quotes_quote_no_seq'::regclass);


--
-- Name: subscription_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_events ALTER COLUMN id SET DEFAULT nextval('public.subscription_events_id_seq'::regclass);


--
-- Name: ticket_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticket_events ALTER COLUMN id SET DEFAULT nextval('public.ticket_events_id_seq'::regclass);


--
-- Name: tickets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tickets ALTER COLUMN id SET DEFAULT nextval('public.tickets_id_seq'::regclass);


--
-- Name: warranty_claims id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warranty_claims ALTER COLUMN id SET DEFAULT nextval('public.warranty_claims_id_seq'::regclass);


--
-- Name: webhook_credentials channel; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_credentials ALTER COLUMN channel SET DEFAULT 'webhook'::text;


--
-- Name: action_log action_log_client_id_action_type_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_log
    ADD CONSTRAINT action_log_client_id_action_type_idempotency_key_key UNIQUE (client_id, action_type, idempotency_key);


--
-- Name: action_log action_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_log
    ADD CONSTRAINT action_log_pkey PRIMARY KEY (id);


--
-- Name: agent_runs agent_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (id);


--
-- Name: approval_audit approval_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approval_audit
    ADD CONSTRAINT approval_audit_pkey PRIMARY KEY (id);


--
-- Name: approvals approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_pkey PRIMARY KEY (id);


--
-- Name: bookings bookings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_pkey PRIMARY KEY (id);


--
-- Name: branches branches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.branches
    ADD CONSTRAINT branches_pkey PRIMARY KEY (id);


--
-- Name: capability_registry capability_registry_client_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capability_registry
    ADD CONSTRAINT capability_registry_client_id_name_key UNIQUE (client_id, name);


--
-- Name: capability_registry capability_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capability_registry
    ADD CONSTRAINT capability_registry_pkey PRIMARY KEY (id);


--
-- Name: channel_credentials channel_credentials_client_id_channel_shop_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channel_credentials
    ADD CONSTRAINT channel_credentials_client_id_channel_shop_id_key UNIQUE (client_id, channel, shop_id);


--
-- Name: channel_credentials channel_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channel_credentials
    ADD CONSTRAINT channel_credentials_pkey PRIMARY KEY (id);


--
-- Name: clients clients_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_pkey PRIMARY KEY (id);


--
-- Name: clients clients_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_slug_key UNIQUE (slug);


--
-- Name: contact_identities contact_identities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_identities
    ADD CONSTRAINT contact_identities_pkey PRIMARY KEY (client_id, channel, external_id);


--
-- Name: contact_verifications contact_verifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_verifications
    ADD CONSTRAINT contact_verifications_pkey PRIMARY KEY (id);


--
-- Name: contacts contacts_client_id_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contacts
    ADD CONSTRAINT contacts_client_id_email_key UNIQUE (client_id, email);


--
-- Name: contacts contacts_client_id_telegram_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contacts
    ADD CONSTRAINT contacts_client_id_telegram_id_key UNIQUE (client_id, telegram_id);


--
-- Name: contacts contacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contacts
    ADD CONSTRAINT contacts_pkey PRIMARY KEY (id);


--
-- Name: conversations conversations_client_id_channel_channel_thread_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_client_id_channel_channel_thread_key UNIQUE (client_id, channel, channel_thread);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);


--
-- Name: dlq dlq_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dlq
    ADD CONSTRAINT dlq_pkey PRIMARY KEY (id);


--
-- Name: ingress_dedup ingress_dedup_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingress_dedup
    ADD CONSTRAINT ingress_dedup_pkey PRIMARY KEY (client_id, idempotency_key);


--
-- Name: ingress_events ingress_events_client_id_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingress_events
    ADD CONSTRAINT ingress_events_client_id_idempotency_key_key UNIQUE (client_id, idempotency_key);


--
-- Name: ingress_events ingress_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingress_events
    ADD CONSTRAINT ingress_events_pkey PRIMARY KEY (id);


--
-- Name: ingress_events ingress_events_trace_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingress_events
    ADD CONSTRAINT ingress_events_trace_id_key UNIQUE (trace_id);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (id);


--
-- Name: kb_chunks kb_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_chunks
    ADD CONSTRAINT kb_chunks_pkey PRIMARY KEY (id);


--
-- Name: kb_documents kb_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_documents
    ADD CONSTRAINT kb_documents_pkey PRIMARY KEY (id);


--
-- Name: kb_gaps kb_gaps_client_id_query_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_gaps
    ADD CONSTRAINT kb_gaps_client_id_query_hash_key UNIQUE (client_id, query_hash);


--
-- Name: kb_gaps kb_gaps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_gaps
    ADD CONSTRAINT kb_gaps_pkey PRIMARY KEY (id);


--
-- Name: kb_ingest_jobs kb_ingest_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_ingest_jobs
    ADD CONSTRAINT kb_ingest_jobs_pkey PRIMARY KEY (id);


--
-- Name: kb_sources kb_sources_client_id_sheet_id_gid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_sources
    ADD CONSTRAINT kb_sources_client_id_sheet_id_gid_key UNIQUE (client_id, sheet_id, gid);


--
-- Name: kb_sources kb_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_sources
    ADD CONSTRAINT kb_sources_pkey PRIMARY KEY (id);


--
-- Name: messages messages_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: order_confirmations order_confirmations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_confirmations
    ADD CONSTRAINT order_confirmations_pkey PRIMARY KEY (id);


--
-- Name: order_confirmations order_confirmations_token_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_confirmations
    ADD CONSTRAINT order_confirmations_token_uq UNIQUE (client_id, token);


--
-- Name: order_events order_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_events
    ADD CONSTRAINT order_events_pkey PRIMARY KEY (id);


--
-- Name: order_items order_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_items
    ADD CONSTRAINT order_items_pkey PRIMARY KEY (id);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: outbound_deliveries outbound_deliveries_message_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_deliveries
    ADD CONSTRAINT outbound_deliveries_message_id_key UNIQUE (message_id);


--
-- Name: outbound_deliveries outbound_deliveries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_deliveries
    ADD CONSTRAINT outbound_deliveries_pkey PRIMARY KEY (id);


--
-- Name: outbound_delivery_attempts outbound_delivery_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_delivery_attempts
    ADD CONSTRAINT outbound_delivery_attempts_pkey PRIMARY KEY (id);


--
-- Name: pending_actions pending_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pending_actions
    ADD CONSTRAINT pending_actions_pkey PRIMARY KEY (id);


--
-- Name: plans plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_pkey PRIMARY KEY (client_id, code);


--
-- Name: price_book_legacy price_book_client_id_sku_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_book_legacy
    ADD CONSTRAINT price_book_client_id_sku_key UNIQUE (client_id, sku);


--
-- Name: price_book_legacy price_book_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_book_legacy
    ADD CONSTRAINT price_book_pkey PRIMARY KEY (id);


--
-- Name: product_channel_map product_channel_map_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_channel_map
    ADD CONSTRAINT product_channel_map_pkey PRIMARY KEY (id);


--
-- Name: products products_client_id_sku_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_client_id_sku_key UNIQUE (client_id, sku);


--
-- Name: products products_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (id);


--
-- Name: quotes quotes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotes
    ADD CONSTRAINT quotes_pkey PRIMARY KEY (id);


--
-- Name: quotes quotes_quote_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotes
    ADD CONSTRAINT quotes_quote_no_key UNIQUE (quote_no);


--
-- Name: refunds refunds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refunds
    ADD CONSTRAINT refunds_pkey PRIMARY KEY (id);


--
-- Name: subscription_events subscription_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_events
    ADD CONSTRAINT subscription_events_pkey PRIMARY KEY (id);


--
-- Name: subscription_requests subscription_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_requests
    ADD CONSTRAINT subscription_requests_pkey PRIMARY KEY (id);


--
-- Name: subscriptions subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_pkey PRIMARY KEY (id);


--
-- Name: ticket_events ticket_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticket_events
    ADD CONSTRAINT ticket_events_pkey PRIMARY KEY (id);


--
-- Name: tickets tickets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tickets
    ADD CONSTRAINT tickets_pkey PRIMARY KEY (id);


--
-- Name: voice_transcripts voice_transcripts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voice_transcripts
    ADD CONSTRAINT voice_transcripts_pkey PRIMARY KEY (id);


--
-- Name: warranty_claims warranty_claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warranty_claims
    ADD CONSTRAINT warranty_claims_pkey PRIMARY KEY (id);


--
-- Name: webhook_endpoints webhook_endpoints_credential_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_endpoints
    ADD CONSTRAINT webhook_endpoints_credential_id_key UNIQUE (credential_id);


--
-- Name: webhook_endpoints webhook_endpoints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_endpoints
    ADD CONSTRAINT webhook_endpoints_pkey PRIMARY KEY (id);


--
-- Name: webhook_endpoints webhook_endpoints_public_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_endpoints
    ADD CONSTRAINT webhook_endpoints_public_id_key UNIQUE (public_id);


--
-- Name: contacts_client_console_user_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX contacts_client_console_user_uq ON public.contacts USING btree (client_id, console_user_id) WHERE (console_user_id IS NOT NULL);


--
-- Name: idx_action_log_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_action_log_client ON public.action_log USING btree (client_id);


--
-- Name: idx_action_log_conversation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_action_log_conversation ON public.action_log USING btree (conversation_id);


--
-- Name: idx_agent_runs_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_agent ON public.agent_runs USING btree (agent, created_at DESC);


--
-- Name: idx_agent_runs_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_client ON public.agent_runs USING btree (client_id);


--
-- Name: idx_agent_runs_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_created ON public.agent_runs USING btree (created_at DESC);


--
-- Name: idx_agent_runs_trace; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_trace ON public.agent_runs USING btree (trace_id);


--
-- Name: idx_approval_audit_approval; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approval_audit_approval ON public.approval_audit USING btree (approval_id);


--
-- Name: idx_approval_audit_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approval_audit_client ON public.approval_audit USING btree (client_id);


--
-- Name: idx_approvals_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approvals_client ON public.approvals USING btree (client_id);


--
-- Name: idx_approvals_conversation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approvals_conversation ON public.approvals USING btree (conversation_id, status);


--
-- Name: idx_approvals_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approvals_pending ON public.approvals USING btree (status, expires_at) WHERE (status = 'pending'::text);


--
-- Name: idx_approvals_trace; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approvals_trace ON public.approvals USING btree (trace_id);


--
-- Name: idx_bookings_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bookings_client ON public.bookings USING btree (client_id);


--
-- Name: idx_bookings_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bookings_contact ON public.bookings USING btree (contact_id);


--
-- Name: idx_bookings_starts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bookings_starts ON public.bookings USING btree (starts_at) WHERE (status = ANY (ARRAY['pending'::text, 'confirmed'::text]));


--
-- Name: idx_branches_active_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_branches_active_client ON public.branches USING btree (client_id) WHERE is_active;


--
-- Name: idx_branches_address_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_branches_address_trgm ON public.branches USING gin (address public.gin_trgm_ops);


--
-- Name: idx_branches_amenities_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_branches_amenities_gin ON public.branches USING gin (amenities jsonb_path_ops);


--
-- Name: idx_branches_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_branches_client ON public.branches USING btree (client_id);


--
-- Name: idx_branches_location_geog; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_branches_location_geog ON public.branches USING gist (((public.st_setsrid(public.st_makepoint(location[0], location[1]), 4326))::public.geography)) WHERE (location IS NOT NULL);


--
-- Name: idx_branches_name_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_branches_name_trgm ON public.branches USING gin (name public.gin_trgm_ops);


--
-- Name: idx_branches_parking_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_branches_parking_gin ON public.branches USING gin (parking_info jsonb_path_ops);


--
-- Name: idx_capability_registry_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_capability_registry_client ON public.capability_registry USING btree (client_id);


--
-- Name: idx_capability_registry_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_capability_registry_enabled ON public.capability_registry USING btree (is_enabled, circuit_state);


--
-- Name: idx_capability_registry_pattern; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_capability_registry_pattern ON public.capability_registry USING btree (pattern);


--
-- Name: idx_clients_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_clients_email ON public.clients USING btree (lower(email)) WHERE ((email IS NOT NULL) AND (btrim(email) <> ''::text));


--
-- Name: idx_clients_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clients_slug ON public.clients USING btree (slug);


--
-- Name: idx_clients_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clients_status ON public.clients USING btree (status) WHERE (status = ANY (ARRAY['pilot'::text, 'active'::text]));


--
-- Name: idx_contact_identities_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contact_identities_contact ON public.contact_identities USING btree (contact_id);


--
-- Name: idx_contact_verifications_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contact_verifications_lookup ON public.contact_verifications USING btree (client_id, contact_id, channel, status, expires_at);


--
-- Name: idx_contacts_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contacts_client ON public.contacts USING btree (client_id);


--
-- Name: idx_contacts_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contacts_email ON public.contacts USING btree (email) WHERE (email IS NOT NULL);


--
-- Name: idx_contacts_telegram; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contacts_telegram ON public.contacts USING btree (telegram_id) WHERE (telegram_id IS NOT NULL);


--
-- Name: idx_conversations_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_client ON public.conversations USING btree (client_id);


--
-- Name: idx_conversations_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_contact ON public.conversations USING btree (contact_id);


--
-- Name: idx_conversations_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_status ON public.conversations USING btree (status) WHERE (status = 'open'::text);


--
-- Name: idx_delivery_attempts_delivery; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_delivery_attempts_delivery ON public.outbound_delivery_attempts USING btree (delivery_id, attempt_no);


--
-- Name: idx_dlq_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dlq_client ON public.dlq USING btree (client_id);


--
-- Name: idx_dlq_replayed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dlq_replayed ON public.dlq USING btree (replayed, abandoned) WHERE ((NOT replayed) AND (NOT abandoned));


--
-- Name: idx_ingress_events_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ingress_events_client ON public.ingress_events USING btree (client_id, created_at DESC);


--
-- Name: idx_ingress_events_dispatch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ingress_events_dispatch ON public.ingress_events USING btree (next_attempt_at) WHERE (status = 'received'::text);


--
-- Name: idx_ingress_events_endpoint; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ingress_events_endpoint ON public.ingress_events USING btree (endpoint_id, created_at DESC);


--
-- Name: idx_ingress_events_inflight; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ingress_events_inflight ON public.ingress_events USING btree (dispatched_at) WHERE (status = 'dispatched'::text);


--
-- Name: idx_invoices_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_invoices_client ON public.invoices USING btree (client_id);


--
-- Name: idx_invoices_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_invoices_contact ON public.invoices USING btree (contact_id);


--
-- Name: idx_invoices_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_invoices_status ON public.invoices USING btree (status);


--
-- Name: idx_kb_chunks_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kb_chunks_client ON public.kb_chunks USING btree (client_id);


--
-- Name: idx_kb_documents_title; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kb_documents_title ON public.kb_documents USING btree (client_id, title);


--
-- Name: idx_kb_gaps_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kb_gaps_open ON public.kb_gaps USING btree (client_id, hits DESC) WHERE (NOT resolved);


--
-- Name: idx_kb_jobs_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kb_jobs_client ON public.kb_ingest_jobs USING btree (client_id, created_at DESC);


--
-- Name: idx_kb_jobs_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kb_jobs_queue ON public.kb_ingest_jobs USING btree (created_at) WHERE (status = 'queued'::text);


--
-- Name: idx_kb_jobs_stuck; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kb_jobs_stuck ON public.kb_ingest_jobs USING btree (locked_at) WHERE (status = 'processing'::text);


--
-- Name: idx_messages_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_client ON public.messages USING btree (client_id);


--
-- Name: idx_messages_conversation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_conversation ON public.messages USING btree (conversation_id, created_at DESC);


--
-- Name: idx_messages_idempotency; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_idempotency ON public.messages USING btree (idempotency_key);


--
-- Name: idx_messages_trace; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_trace ON public.messages USING btree (trace_id) WHERE (trace_id IS NOT NULL);


--
-- Name: idx_orders_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_client ON public.orders USING btree (client_id);


--
-- Name: idx_orders_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_contact ON public.orders USING btree (contact_id);


--
-- Name: idx_orders_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_status ON public.orders USING btree (status);


--
-- Name: idx_outbound_deliveries_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outbound_deliveries_client ON public.outbound_deliveries USING btree (client_id, created_at DESC);


--
-- Name: idx_outbound_deliveries_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outbound_deliveries_due ON public.outbound_deliveries USING btree (next_attempt_at) WHERE (status = ANY (ARRAY['pending'::text, 'delivering'::text]));


--
-- Name: idx_pending_actions_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pending_actions_lookup ON public.pending_actions USING btree (conversation_id, agent, status, expires_at);


--
-- Name: idx_price_book_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_price_book_client ON public.price_book_legacy USING btree (client_id);


--
-- Name: idx_quotes_client_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quotes_client_contact ON public.quotes USING btree (client_id, contact_id, created_at DESC);


--
-- Name: idx_quotes_trace; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quotes_trace ON public.quotes USING btree (trace_id);


--
-- Name: idx_refunds_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refunds_client ON public.refunds USING btree (client_id);


--
-- Name: idx_refunds_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refunds_order ON public.refunds USING btree (order_id);


--
-- Name: idx_sub_events_sub; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sub_events_sub ON public.subscription_events USING btree (subscription_id, created_at DESC);


--
-- Name: idx_sub_requests_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sub_requests_open ON public.subscription_requests USING btree (subscription_id, type) WHERE (status = 'open'::text);


--
-- Name: idx_subscriptions_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscriptions_client ON public.subscriptions USING btree (client_id);


--
-- Name: idx_subscriptions_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subscriptions_contact ON public.subscriptions USING btree (contact_id);


--
-- Name: idx_ticket_events_ticket; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ticket_events_ticket ON public.ticket_events USING btree (ticket_id, created_at);


--
-- Name: idx_tickets_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tickets_client ON public.tickets USING btree (client_id);


--
-- Name: idx_tickets_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tickets_contact ON public.tickets USING btree (client_id, contact_id, status);


--
-- Name: idx_tickets_conversation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tickets_conversation ON public.tickets USING btree (conversation_id);


--
-- Name: idx_tickets_sla; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tickets_sla ON public.tickets USING btree (sla_due_at) WHERE (status = 'open'::text);


--
-- Name: idx_tickets_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tickets_status ON public.tickets USING btree (status) WHERE (status = 'open'::text);


--
-- Name: idx_voice_transcripts_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_voice_transcripts_client ON public.voice_transcripts USING btree (client_id);


--
-- Name: idx_voice_transcripts_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_voice_transcripts_job ON public.voice_transcripts USING btree (livekit_job_id);


--
-- Name: idx_warranty_claims_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_warranty_claims_client ON public.warranty_claims USING btree (client_id);


--
-- Name: idx_warranty_claims_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_warranty_claims_contact ON public.warranty_claims USING btree (contact_id);


--
-- Name: idx_webhook_endpoints_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_webhook_endpoints_client ON public.webhook_endpoints USING btree (client_id);


--
-- Name: order_conf_contact_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX order_conf_contact_idx ON public.order_confirmations USING btree (client_id, contact_id);


--
-- Name: order_conf_one_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX order_conf_one_pending ON public.order_confirmations USING btree (order_id) WHERE (state = 'pending'::text);


--
-- Name: order_events_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX order_events_order_idx ON public.order_events USING btree (client_id, order_id, created_at DESC);


--
-- Name: order_items_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX order_items_order_idx ON public.order_items USING btree (client_id, order_id);


--
-- Name: order_items_product_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX order_items_product_idx ON public.order_items USING btree (product_id);


--
-- Name: orders_external_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX orders_external_uq ON public.orders USING btree (client_id, channel, external_order_id) WHERE (external_order_id IS NOT NULL);


--
-- Name: orders_idem_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX orders_idem_uq ON public.orders USING btree (client_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);


--
-- Name: orders_recent_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orders_recent_idx ON public.orders USING btree (client_id, contact_id, placed_at DESC NULLS LAST);


--
-- Name: pcm_product_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pcm_product_idx ON public.product_channel_map USING btree (product_id);


--
-- Name: pcm_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX pcm_uq ON public.product_channel_map USING btree (client_id, channel, external_product_id, COALESCE(external_sku_id, ''::text));


--
-- Name: products_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX products_active_idx ON public.products USING btree (client_id) WHERE is_active;


--
-- Name: products_cat_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX products_cat_idx ON public.products USING btree (client_id, category);


--
-- Name: products_category_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX products_category_idx ON public.products USING btree (client_id, category);


--
-- Name: products_desc_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX products_desc_trgm_idx ON public.products USING gin (description public.gin_trgm_ops);


--
-- Name: products_name_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX products_name_trgm ON public.products USING gin (name public.gin_trgm_ops);


--
-- Name: products_name_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX products_name_trgm_idx ON public.products USING gin (name public.gin_trgm_ops);


--
-- Name: uq_kb_chunks_doc_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_kb_chunks_doc_idx ON public.kb_chunks USING btree (document_id, chunk_index);


--
-- Name: uq_kb_documents_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_kb_documents_hash ON public.kb_documents USING btree (client_id, content_hash) WHERE (is_active AND (content_hash IS NOT NULL));


--
-- Name: uq_voice_transcripts_livekit_job; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_voice_transcripts_livekit_job ON public.voice_transcripts USING btree (livekit_job_id) WHERE (livekit_job_id IS NOT NULL);


--
-- Name: tickets tickets_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER tickets_set_updated_at BEFORE UPDATE ON public.tickets FOR EACH ROW EXECUTE FUNCTION public.tickets_touch_updated_at();


--
-- Name: contact_identities trg_contact_identity_tenant; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_contact_identity_tenant BEFORE INSERT OR UPDATE ON public.contact_identities FOR EACH ROW EXECUTE FUNCTION public.contact_identity_tenant_check();


--
-- Name: conversations trg_keep_escalated; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_keep_escalated BEFORE UPDATE OF status ON public.conversations FOR EACH ROW EXECUTE FUNCTION public.keep_escalated_status();


--
-- Name: order_items trg_sync_order_items; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sync_order_items AFTER INSERT OR DELETE OR UPDATE ON public.order_items FOR EACH ROW EXECUTE FUNCTION public.sync_order_items_json();


--
-- Name: messages trg_webhook_delivery; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_webhook_delivery AFTER INSERT ON public.messages FOR EACH ROW WHEN ((new.direction = 'outbound'::text)) EXECUTE FUNCTION public.enqueue_webhook_delivery();


--
-- Name: webhook_endpoints trg_webhook_endpoints_updated; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_webhook_endpoints_updated BEFORE UPDATE ON public.webhook_endpoints FOR EACH ROW EXECUTE FUNCTION public.webhook_set_updated_at();


--
-- Name: action_log action_log_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_log
    ADD CONSTRAINT action_log_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: agent_runs agent_runs_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: approval_audit approval_audit_approval_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approval_audit
    ADD CONSTRAINT approval_audit_approval_id_fkey FOREIGN KEY (approval_id) REFERENCES public.approvals(id);


--
-- Name: approval_audit approval_audit_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approval_audit
    ADD CONSTRAINT approval_audit_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: approvals approvals_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: bookings bookings_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: bookings bookings_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id);


--
-- Name: branches branches_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.branches
    ADD CONSTRAINT branches_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: capability_registry capability_registry_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capability_registry
    ADD CONSTRAINT capability_registry_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: channel_credentials channel_credentials_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channel_credentials
    ADD CONSTRAINT channel_credentials_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;


--
-- Name: contact_identities contact_identities_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_identities
    ADD CONSTRAINT contact_identities_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: contact_identities contact_identities_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_identities
    ADD CONSTRAINT contact_identities_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id) ON DELETE CASCADE;


--
-- Name: contact_verifications contact_verifications_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_verifications
    ADD CONSTRAINT contact_verifications_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: contact_verifications contact_verifications_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_verifications
    ADD CONSTRAINT contact_verifications_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id);


--
-- Name: contacts contacts_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contacts
    ADD CONSTRAINT contacts_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: conversations conversations_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: conversations conversations_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id);


--
-- Name: dlq dlq_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dlq
    ADD CONSTRAINT dlq_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: ingress_dedup ingress_dedup_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingress_dedup
    ADD CONSTRAINT ingress_dedup_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;


--
-- Name: ingress_events ingress_events_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingress_events
    ADD CONSTRAINT ingress_events_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: ingress_events ingress_events_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingress_events
    ADD CONSTRAINT ingress_events_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id);


--
-- Name: ingress_events ingress_events_endpoint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingress_events
    ADD CONSTRAINT ingress_events_endpoint_id_fkey FOREIGN KEY (endpoint_id) REFERENCES public.webhook_endpoints(id);


--
-- Name: invoices invoices_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: invoices invoices_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id);


--
-- Name: invoices invoices_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: kb_chunks kb_chunks_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_chunks
    ADD CONSTRAINT kb_chunks_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: kb_chunks kb_chunks_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_chunks
    ADD CONSTRAINT kb_chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.kb_documents(id) ON DELETE CASCADE;


--
-- Name: kb_documents kb_documents_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_documents
    ADD CONSTRAINT kb_documents_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: kb_gaps kb_gaps_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_gaps
    ADD CONSTRAINT kb_gaps_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: kb_ingest_jobs kb_ingest_jobs_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_ingest_jobs
    ADD CONSTRAINT kb_ingest_jobs_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: kb_ingest_jobs kb_ingest_jobs_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_ingest_jobs
    ADD CONSTRAINT kb_ingest_jobs_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.kb_sources(id) ON DELETE SET NULL;


--
-- Name: kb_sources kb_sources_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kb_sources
    ADD CONSTRAINT kb_sources_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: messages messages_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: messages messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;


--
-- Name: order_confirmations order_confirmations_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_confirmations
    ADD CONSTRAINT order_confirmations_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;


--
-- Name: order_confirmations order_confirmations_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_confirmations
    ADD CONSTRAINT order_confirmations_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id) ON DELETE CASCADE;


--
-- Name: order_confirmations order_confirmations_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_confirmations
    ADD CONSTRAINT order_confirmations_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE CASCADE;


--
-- Name: order_events order_events_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_events
    ADD CONSTRAINT order_events_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;


--
-- Name: order_events order_events_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_events
    ADD CONSTRAINT order_events_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE CASCADE;


--
-- Name: order_items order_items_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_items
    ADD CONSTRAINT order_items_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;


--
-- Name: order_items order_items_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_items
    ADD CONSTRAINT order_items_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE CASCADE;


--
-- Name: order_items order_items_product_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_items
    ADD CONSTRAINT order_items_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(id);


--
-- Name: orders orders_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: orders orders_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id);


--
-- Name: outbound_deliveries outbound_deliveries_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_deliveries
    ADD CONSTRAINT outbound_deliveries_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: outbound_deliveries outbound_deliveries_endpoint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_deliveries
    ADD CONSTRAINT outbound_deliveries_endpoint_id_fkey FOREIGN KEY (endpoint_id) REFERENCES public.webhook_endpoints(id);


--
-- Name: outbound_deliveries outbound_deliveries_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_deliveries
    ADD CONSTRAINT outbound_deliveries_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id) ON DELETE CASCADE;


--
-- Name: outbound_delivery_attempts outbound_delivery_attempts_delivery_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_delivery_attempts
    ADD CONSTRAINT outbound_delivery_attempts_delivery_id_fkey FOREIGN KEY (delivery_id) REFERENCES public.outbound_deliveries(id) ON DELETE CASCADE;


--
-- Name: pending_actions pending_actions_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pending_actions
    ADD CONSTRAINT pending_actions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: pending_actions pending_actions_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pending_actions
    ADD CONSTRAINT pending_actions_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id);


--
-- Name: pending_actions pending_actions_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pending_actions
    ADD CONSTRAINT pending_actions_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id);


--
-- Name: plans plans_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: price_book_legacy price_book_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_book_legacy
    ADD CONSTRAINT price_book_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: product_channel_map product_channel_map_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_channel_map
    ADD CONSTRAINT product_channel_map_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;


--
-- Name: product_channel_map product_channel_map_product_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_channel_map
    ADD CONSTRAINT product_channel_map_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(id) ON DELETE CASCADE;


--
-- Name: products products_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;


--
-- Name: quotes quotes_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotes
    ADD CONSTRAINT quotes_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: refunds refunds_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refunds
    ADD CONSTRAINT refunds_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: refunds refunds_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refunds
    ADD CONSTRAINT refunds_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: subscription_events subscription_events_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_events
    ADD CONSTRAINT subscription_events_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: subscription_events subscription_events_subscription_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_events
    ADD CONSTRAINT subscription_events_subscription_id_fkey FOREIGN KEY (subscription_id) REFERENCES public.subscriptions(id);


--
-- Name: subscription_requests subscription_requests_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_requests
    ADD CONSTRAINT subscription_requests_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: subscription_requests subscription_requests_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_requests
    ADD CONSTRAINT subscription_requests_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id);


--
-- Name: subscription_requests subscription_requests_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_requests
    ADD CONSTRAINT subscription_requests_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id);


--
-- Name: subscription_requests subscription_requests_subscription_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscription_requests
    ADD CONSTRAINT subscription_requests_subscription_id_fkey FOREIGN KEY (subscription_id) REFERENCES public.subscriptions(id);


--
-- Name: subscriptions subscriptions_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: subscriptions subscriptions_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id);


--
-- Name: ticket_events ticket_events_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticket_events
    ADD CONSTRAINT ticket_events_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: ticket_events ticket_events_ticket_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticket_events
    ADD CONSTRAINT ticket_events_ticket_id_fkey FOREIGN KEY (ticket_id) REFERENCES public.tickets(id);


--
-- Name: tickets tickets_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tickets
    ADD CONSTRAINT tickets_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: tickets tickets_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tickets
    ADD CONSTRAINT tickets_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id);


--
-- Name: tickets tickets_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tickets
    ADD CONSTRAINT tickets_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id);


--
-- Name: voice_transcripts voice_transcripts_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voice_transcripts
    ADD CONSTRAINT voice_transcripts_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: voice_transcripts voice_transcripts_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voice_transcripts
    ADD CONSTRAINT voice_transcripts_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id);


--
-- Name: warranty_claims warranty_claims_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warranty_claims
    ADD CONSTRAINT warranty_claims_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: warranty_claims warranty_claims_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warranty_claims
    ADD CONSTRAINT warranty_claims_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.contacts(id);


--
-- Name: warranty_claims warranty_claims_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.warranty_claims
    ADD CONSTRAINT warranty_claims_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: webhook_endpoints webhook_endpoints_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_endpoints
    ADD CONSTRAINT webhook_endpoints_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: webhook_endpoints webhook_endpoints_credential_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_endpoints
    ADD CONSTRAINT webhook_endpoints_credential_id_fkey FOREIGN KEY (credential_id) REFERENCES public.channel_credentials(id);


--
-- PostgreSQL database dump complete
--

\unrestrict hhqIrgfYPj13adCheTdPTFAjE4DBVpUoT3OkuA6Kcm8yCtP0mwwfd9zZr19n559
