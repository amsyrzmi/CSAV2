# Console API contract (v1, draft)

For the web-console developer. The console backend (not the browser) calls this API.

## Auth and tenancy
- Enabled only when `CONSOLE_API_TOKEN` (≥ 32 chars) is set on the gateway; otherwise `/console/v1/*` does not exist.
- Every request: `Authorization: Bearer <token>` and `X-Client-Id: <tenant uuid>`.
  The console backend authenticates its own users (admin / client) and decides which tenant they may see.
  Tenant isolation is also enforced in Postgres (RLS), so a wrong id returns empty data, never another tenant's.
- Never expose the token to browsers. 401 = bad token; 422 = missing / malformed `X-Client-Id`.

## Endpoints
### `GET /console/v1/traces?limit=50&before=<ISO time>`
One row per customer turn, newest first. Page by passing the last `started_at` as `before`.
```json
{"trace_id":"…","conversation_id":"…","started_at":"…","finished_at":"…","steps":14,"had_error":false,
 "route":"plan_args","intent":"order","gate_decision":"send","agents":["order_agent"]}
```
`gate_decision`: `send` (sent), `approve` (held for staff), `reject` (shadow mode, nothing sent).

### `GET /console/v1/events?trace_id=<uuid>&after_id=0&limit=100`
Ordered timeline steps. Poll for live updates by passing the largest `id` you have as `after_id`
(every 1–2 s while a trace is open). `trace_id` is optional (omit for a tenant-wide live feed).
```json
{"id":812,"trace_id":"…","conversation_id":"…","node":"run_spoke","agent":"order_agent",
 "status":"ok","duration_ms":41,"summary":{"task_id":"t1","ok":true,"agent_status":"ok"},"created_at":"…"}
```

### Nodes and summary keys
| node | meaning | summary keys |
|---|---|---|
| load_ctx, guard | context loaded, safety check | – |
| signals | configured classifier judged the message | `intent`, `provider` (`llm`, `jev` or callback `policy`) |
| route | policy chose a path | `route` |
| fast_plan / plan_args | plan made | `intent`, `strategy`, `confidence`, `tasks` |
| run_spoke | an agent ran (`agent` set) | `task_id`, `ok`, `agent_status`, `error_code`, `requires_human` |
| collect | results merged into facts | – |
| observe | agent loop looked at the results and chose read-only follow-ups | `step`, `next_tasks`, `loop_stop` |
| handoff | escalation decision | `handoff_required`, `handoff_reason` |
| compose / verify | reply written / checked | `language`, `fact_count`, `verified`, `verify_issue_count` |
| gate | policy verdict | `decision`, `reason`, `risk` |
| hold / emit | reply held for staff / queued to send | `messages` |
| finalize / fail_safe | turn closed / safe fallback used | `error` |

**Agent loop.** After the first agents run, `observe` may start another round of read-only lookups
(for example product details after a search). Follow-up `run_spoke` events have task ids `s<step>-<n>-<agent>`,
so the console can draw rounds: `plan → run_spoke × n → observe (step 1) → run_spoke → observe (step 2)`.
`loop_stop` says why the loop ended: `done`, `rejected:not_read_only`, `rejected:duplicate`,
`rejected:over_budget` or `observe_failed:<error>`. Turns that are not LLM-planned have no `observe` event.

`status` is `error` when the step failed (`summary.error` has the code). Summaries never contain customer text
or secrets; they are codes, counts and names, so they are safe to render.
Semantic rejection details stay in turn state for regeneration; only their count appears on the timeline.
Classifier comparisons are persisted separately in `decision_log`; a console endpoint for those rows
is not implemented yet.

## Capabilities and specialised tabs (LG13)
- `GET /console/v1/capabilities`: each agent has `name`, `is_enabled` (the admin subscription), `client_enabled` (the client's own switch), `active` (true only when both are on), `circuit_state`, `fail_count`, `capability_kind`, and `updated_at`.
- `PATCH /console/v1/capabilities/{name}` with body `{"is_enabled"?: bool, "client_enabled"?: bool, "changed_by": "<user>"}`. **Warning:** the shared token can currently change both fields. Restricting `is_enabled` to platform admins is part of LG15.
- `GET /console/v1/tabs/specialized`: the tab catalogue (`lhdn_myinvois`, `ecommerce`, `knowledge_base`, `invoices`, `bookings`). Each tab has `is_subscribed`, `is_active`, and `metrics`. Show a tab only when `is_active` is true.

## Knowledge base (LG13)
- `GET /console/v1/kb/documents?limit=50` lists documents with `version`, `is_active`, `file_name`, `chunk_count`, `kind`, and `embed_model`.
- `POST /console/v1/kb/documents` adds plain text. Body: `{title, content, category?, language?, source_uri?}`. Returns 503 when the embedding service is down.
- `DELETE /console/v1/kb/documents/{id}` deletes a document.
- File upload: the console writes up to 3 PDF/DOCX/CSV files to `KB_UPLOAD_ROOT/<client_id>/` and inserts a `kb_ingest_jobs` row. See the KB upload contract in LG13. Until LG15 adds an upload endpoint, the console must write the row through the tenant-scoped database role.
- `GET /console/v1/kb/ingest-jobs?limit=20` and `GET /console/v1/kb/ingest-jobs/{id}` return the job `status` (`queued`, `processing`, `done`, `partial` or `failed`) and per-file `meta.results`.
- `GET /console/v1/kb/gaps` lists questions the KB could not answer. `POST /console/v1/kb/gaps/{id}/resolve` marks one resolved; it does not accept an answer text yet.

## Invoices (LG13)
- `GET /console/v1/invoices?limit=50` lists invoices.
- `GET /console/v1/invoices/{id}/download-link` returns a signed link that expires after 15 minutes. **Known gap:** the target route `/api/v1/invoices/{id}/download` is not served yet, and link signing falls back to a placeholder key when settings fail to load. Do not use this endpoint in production until LG15 fixes both.

## OpenAPI
The gateway serves FastAPI's `/openapi.json` and `/docs`. Generate a typed client from them. LG15 will restrict both outside dev.

## Not built yet (planned, see LG15)
Server-sent live stream, conversations/messages read API, takeover/release/human reply, approvals queue and decide endpoints, policy and
agent settings editing, channel/integration connect, notification targets, DLQ/runs, usage/billing, per-user roles (RBAC), and a KB upload endpoint. Tell us which screen you build first.

## Questions for the console developer
1. Does the console backend share a trust boundary with the CSA host (shared token OK) or must it be per-tenant?
2. Which first screens: live agent activity, conversation inbox, approvals queue, settings?
3. Is polling every 1–2 s acceptable, or do you need SSE / WebSocket?
