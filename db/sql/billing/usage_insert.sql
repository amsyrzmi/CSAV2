-- One ledger row for a model call. Run inside tx() for the company (row security on app.client_id);
-- occurred_at takes its default.
INSERT INTO ai_usage_events (
    client_id, trace_id, conversation_id, job, provider, model,
    prompt_tokens, completion_tokens, schema_name
) VALUES (
    %(client_id)s, %(trace_id)s, %(conversation_id)s, %(job)s, %(provider)s, %(model)s,
    %(prompt_tokens)s, %(completion_tokens)s, %(schema_name)s
);
