-- One synthetic ledger row for the credit demo (scripts/seed_credit_demo.py). The same columns and
-- constraints as billing/usage_insert.sql, plus an explicit occurred_at: the demo spreads its calls
-- over two billing cycles, where the app's own insert stamps now(). Run inside the company's scope.
INSERT INTO public.ai_usage_events (
    client_id, occurred_at, trace_id, conversation_id, job, provider, model,
    prompt_tokens, completion_tokens, schema_name
) VALUES (
    %(client_id)s, %(occurred_at)s, %(trace_id)s, %(conversation_id)s, %(job)s, %(provider)s,
    %(model)s, %(prompt_tokens)s, %(completion_tokens)s, %(schema_name)s
);
