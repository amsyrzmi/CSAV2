-- Preconditions: 0430 applied; additive sandbox media-session foundation.
-- Rollback: disable interactive voice, stop its media worker, drop the new
-- utterance/session tables and voice_lock_actor(), then the new turn constraint.
-- Existing text/recorded-voice data and legacy owner access remain intact.
ALTER TABLE public.console_chat_turns ADD CONSTRAINT console_chat_turns_voice_binding
    UNIQUE (id, client_id, conversation_id, request_key, trace_id);

CREATE TABLE public.console_voice_sessions (
    id uuid PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    user_id uuid NOT NULL REFERENCES public.console_users(id),
    login_session_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    request_key uuid NOT NULL,
    profile text NOT NULL DEFAULT 'sandbox' CHECK (profile = 'sandbox'),
    language text NOT NULL CHECK (language IN ('en','ms')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','ended')),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > created_at AND expires_at <= created_at + interval '15 minutes'),
    UNIQUE (client_id, user_id, login_session_id, request_key),
    UNIQUE (id, client_id, user_id, conversation_id),
    FOREIGN KEY (login_session_id, user_id) REFERENCES public.console_sessions(id, user_id),
    FOREIGN KEY (conversation_id, client_id, user_id)
        REFERENCES public.console_chat_threads(id, client_id, user_id)
);
CREATE TABLE public.console_voice_utterances (
    session_id uuid NOT NULL,
    utterance_id uuid NOT NULL,
    client_id uuid NOT NULL,
    user_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    request_key uuid NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    turn_id bigint NOT NULL,
    trace_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (session_id, utterance_id),
    UNIQUE (client_id, conversation_id, request_key),
    FOREIGN KEY (session_id, client_id, user_id, conversation_id)
        REFERENCES public.console_voice_sessions(id, client_id, user_id, conversation_id),
    FOREIGN KEY (turn_id, client_id, conversation_id, request_key, trace_id)
        REFERENCES public.console_chat_turns(id, client_id, conversation_id, request_key, trace_id)
);

ALTER TABLE public.console_voice_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.console_voice_sessions
    USING (client_id = nullif(current_setting('app.client_id',true),'')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id',true),'')::uuid);
ALTER TABLE public.console_voice_utterances ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.console_voice_utterances
    USING (client_id = nullif(current_setting('app.client_id',true),'')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id',true),'')::uuid);
REVOKE ALL ON public.console_voice_sessions, public.console_voice_utterances
    FROM PUBLIC, csa_app, csa_ro;
GRANT SELECT, INSERT, UPDATE ON public.console_voice_sessions TO csa_app;
GRANT SELECT, INSERT ON public.console_voice_utterances TO csa_app;

-- Trusted adapter gate: hold identity locks through the domain transaction.
-- Login/session/user values come from authenticated actor or stored session,
-- never from media text or browser identity headers. Domain roles cannot read
-- global identity tables. Existing identity revokers conflict with these locks.
CREATE FUNCTION public.voice_lock_actor(p_session uuid, p_user uuid, p_client uuid, p_idle integer)
RETURNS timestamptz LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE u public.console_users%ROWTYPE; s public.console_sessions%ROWTYPE;
        m public.console_memberships%ROWTYPE;
BEGIN
    IF session_user <> 'csa_app' OR p_idle NOT BETWEEN 60 AND 86400
        OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid
    THEN RETURN NULL; END IF;
    SELECT * INTO u FROM public.console_users WHERE id=p_user FOR SHARE;
    IF NOT FOUND OR NOT u.is_enabled OR u.must_change_password THEN RETURN NULL; END IF;
    SELECT * INTO s FROM public.console_sessions WHERE id=p_session AND user_id=p_user FOR SHARE;
    IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at <= clock_timestamp()
        OR s.last_seen_at <= clock_timestamp()-make_interval(secs => p_idle)
    THEN RETURN NULL; END IF;
    IF u.is_platform_admin THEN
        RETURN LEAST(s.expires_at, s.last_seen_at+make_interval(secs => p_idle));
    END IF;
    IF s.client_id IS DISTINCT FROM p_client THEN RETURN NULL; END IF;
    SELECT * INTO m FROM public.console_memberships
        WHERE user_id=p_user AND client_id=p_client FOR SHARE;
    IF NOT FOUND OR NOT m.is_enabled OR m.role NOT IN ('tenant_admin','tenant_staff')
    THEN RETURN NULL; END IF;
    RETURN LEAST(s.expires_at, s.last_seen_at+make_interval(secs => p_idle));
END $$;
REVOKE ALL ON FUNCTION public.voice_lock_actor(uuid,uuid,uuid,integer)
    FROM PUBLIC, csa_identity, csa_ro;
GRANT EXECUTE ON FUNCTION public.voice_lock_actor(uuid,uuid,uuid,integer) TO csa_app;
