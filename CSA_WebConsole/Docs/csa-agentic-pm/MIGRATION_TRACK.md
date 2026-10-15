# Parallel track — Hosting and n8n → LangGraph cutover

This track applies to the existing CSA customer-service integration. It is separate from the PM planner in phase 04. Read phase 00's dependency inventory and MANUAL_RUNBOOK.md first. Do not retire n8n merely because the new PM graph works.

## IDE AI implementation brief

1. Inspect the existing chat, files, voice, polling, health and telemetry code and their real request/response schemas. Capture contract fixtures from tests or sanitized sandbox examples, not raw production payloads.
2. Extract provider-neutral services without changing the browser API contract. Suggested application interfaces: ChatProvider.send/poll, FilesProvider.ingest/delete, VoiceProvider.submit/status and RuntimeHealthProvider.overview/runs. These are new application interfaces, not native LangGraph/n8n methods.
3. Keep an N8nProvider implementation delegating to current webhooks/API. It should preserve existing headers, trace IDs, timeouts, idempotency and error mappings. Refactoring alone must not change successful behavior.
4. Implement LangGraphProvider only for the functions actually ported. Use durable dispatch/jobs and checkpoints, bounded execution and the same externally visible turn IDs/status semantics. Do not return a fake success for an unported transcriber or document pipeline.
5. Store the chosen provider on each new turn/job, with trace and idempotency references. Poll/cancel/status operations resolve the original provider from server-side state; a new global flag must not send an in-flight turn to another provider.
6. Configure independent provider selectors for chat, files and voice, preserving n8n defaults. Selection can be scoped to a configured sandbox tenant before broader promotion. Keep selection server-owned; browser request bodies cannot bypass routing policy.
7. Ensure both runtime paths honor tenant capability switches, disabled/circuit states and human-required rules. Preserve domain reader compatibility or propose a separately reviewed writer/schema change; PM must not borrow the limited domain_write_engine to write arbitrary conversations/runs.
8. Add provider-aware operational health and telemetry. Keep n8n executions and workflow links visible for n8n-backed agents. LangGraph-backed runs show graph/runtime state rather than a fictitious workflow URL or workflow_off error.
9. Track in-flight jobs across release/rollback and reconciliation. After an unknown dispatch outcome, resolve existing provider state before retrying. A routing change never means sending the same business action to both providers.
10. Update STATE.md and the provider compatibility ledger only with implemented and verified functions.

## Compatibility ledger template

| Function | n8n retained | LangGraph implemented | Contract tests | Staging smoke | Active routing |
|---|---|---|---|---|---|
| Chat send/poll | Yes | Pending | Pending | Pending | n8n |
| File upload/ingest/delete | Yes | Pending | Pending | Pending | n8n |
| Voice fetch/transcribe/status | Yes | Pending | Pending | Pending | n8n |
| Health/toggle behavior | Yes | Pending | Pending | Pending | n8n |
| Execution/domain telemetry | Yes | Pending | Pending | Pending | n8n |
| External channels/workflows | Inventory first | Pending | Pending | Pending | Existing routing |

## Acceptance checks

- Provider refactor passes existing tests with n8n selected.
- Chat turn can be submitted and polled through the old browser contract with LangGraph selected for a sandbox tenant.
- Duplicate submission returns the same turn/action; two providers never perform the same live write.
- Pending turn still polls correctly after the global default changes or the HTTP API restarts.
- File deletion confirms upstream success before removing local metadata, as the existing design requires.
- Voice fetch is reachable from the selected runtime; expired/tampered signed URLs fail correctly; unsupported formats/transcription produce truthful errors.
- Agent toggles affect both implemented runtime paths; monitoring reflects provider-specific health.
- Rollback sends only new requests to n8n; in-flight LangGraph operations remain discoverable and are reconciled without duplication.

## Operator-only steps

Use your existing credential provisioning flow for model access, provider endpoints and any separately authorized domain writer. Confirm homeserver reachability, storage volumes, TLS/Origin/cookie settings and voice deployment. Run each replacement in staging with sandbox clients before selecting it in production. Exact service names and commands require actual repository/deployment context; do not treat the placeholders in the manual runbook as discovered infrastructure.
