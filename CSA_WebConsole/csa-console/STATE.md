# Project State: CSA Console

> Historical source reference only. This describes the copied console before its
> CSAV2 migration, including deferred PM features; it is not current runtime or
> deployment guidance. Do not execute its live-system/tunnel/setup instructions.
> Continue from the [current migration plan](../../docs/archive/handoffs/2026-10-15-console-source-migration-plan.md)
> and [adopted frontend guide](./web/README.md). Its PM deliverable pack was removed
> on 2026-10-20 as obsolete n8n-era design material; this file remains only as a
> dated historical source snapshot.

## 1. Overview & System Purpose
The **CSA Console** (Customer Service AI Console) is a multi-tenant web application and operational control center for the CSA platform. It provides:
1. **Platform Operations (Admin)**: Real-time monitoring of customer service AI capabilities, agent health states, operational KPIs, n8n workflow execution telemetry, client tenant onboarding, team access control, and immutable audit logs.
2. **Client Portal (Tenant Workspace)**: Tenant-scoped workspace allowing business owners and team members to review their active AI assistants, toggle optional capabilities, inspect conversation activity, manage projects and channel bindings, upload/manage knowledge base documents (FAQ), and converse directly with the assistant via rich text, document attachments, and voice notes.

The console acts as a secure, authenticated proxy between browser clients and internal/remote infrastructure components, enforcing multi-tenancy, rate limiting, and PII sanitization.

---

## 2. Architecture & Network Topology

### 2.1 Component Architecture ("Dev-Live" Environment)
In local development, the system runs side-by-side on the developer machine and establishes secure SSH and Tailscale tunnels to the live production server (`nexusserver`):

```
                        ┌────────────────────────────────────────────────────────┐
                        │                  DEVELOPER MACHINE                     │
                        │                                                        │
┌────────────────┐      │   ┌───────────────┐     Rewrites     ┌───────────────┐ │
│  Web Browser   │ ───▶ │   │ Next.js 16    │ ───────────────▶ │ FastAPI       │ │
│ (Admin/Portal) │      │   │ Port: 3001    │   (/api/*)       │ Port: 8100    │ │
└────────────────┘      │   └───────────────┘                  └───┬───────┬───┘ │
                        │                                          │       │     │
                        │   ┌───────────────┐                      │       │     │
                        │   │ Docker PG     │ ◀────────────────────┘       │     │
                        │   │ (console_db)  │  app_engine                  │     │
                        │   │ Port: 5440    │                              │     │
                        │   └───────────────┘                              │     │
                        │                                                  │     │
                        │   ┌───────────────┐                              │     │
                        │   │ Voice Server  │ ◀── Lifespan                 │     │
                        │   │ Port: 8090    │     Background Task          │     │
                        │   └───────┬───────┘                              │     │
                        └───────────┼──────────────────────────────────────┼─────┘
                                    │                                      │
               Tailscale Mesh       │                                      │ SSH Tunnel
         (VOICE_AUDIO_BASE_URL)     │                                      │ (Local Port Forwards)
                                    ▼                                      ▼
                        ┌────────────────────────────────────────────────────────┐
                        │             NEXUSSERVER (REMOTE HOMESERVER)            │
                        │                                                        │
                        │   ┌────────────────────────────────────────────────┐   │
                        │   │ Remote Postgres (csa_db)                       │   │
                        │   │ Local port: 5433 (csa_console_ro)              │   │
                        │   └────────────────────────────────────────────────┘   │
                        │   ┌────────────────────────────────────────────────┐   │
                        │   │ Remote n8n API & Webhooks                      │   │
                        │   │ Local port: 5679 (API) & Webhook Endpoints     │   │
                        │   └────────────────────────────────────────────────┘   │
                        │   ┌────────────────────────────────────────────────┐   │
                        │   │ Remote Coordinator (csa_mcp)                   │   │
                        │   │ Local port: 8001 (/health)                     │   │
                        │   └────────────────────────────────────────────────┘   │
                        └────────────────────────────────────────────────────────┘
```

### 2.2 Network Ports & Protocol Map
| Service | Host / Interface | Port | Purpose |
| :--- | :--- | :--- | :--- |
| **Web Frontend** | `127.0.0.1` | **3001** | Next.js 16 App Router UI (configured in `.env` / Makefile to prevent port 3000 collision). |
| **API Backend** | `127.0.0.1` | **8100** | FastAPI core application serving all `/api/*` endpoints. |
| **Voice Audio Server** | `0.0.0.0` | **8090** | Internal authenticated audio server for voice note retrieval via signed URLs. |
| **Local App DB** | `127.0.0.1` | **5440** | PostgreSQL 16 container (`console_db`) storing console schemas (`app`). |
| **Remote Domain DB** | `127.0.0.1` | **5433** | SSH tunnel to remote PostgreSQL (`csa_db`). Read-only for `domain_engine`, restricted write for `domain_write_engine`. |
| **Remote n8n API** | `127.0.0.1` | **5679** | SSH tunnel forwarding to remote n8n instance port `5678`. |
| **Remote Coordinator**| `127.0.0.1` | **8001** | SSH tunnel forwarding to remote `csa_mcp` coordinator health probe. |

---

## 3. Databases & Data Isolation

### 3.1 App Database (`console_db`, Port 5440)
Managed via local Docker compose (`docker-compose.dev.yml`). Holds private application state in the `app` schema:
- **`app.users`**: User identities, bcrypt/Argon2id password hashes, roles (`platform_admin`, `client_owner`, `client_member`), soft references to `client_id`, `must_change_password` flags, and activity timestamps.
- **`app.sessions`**: Stateful cookie-backed sessions (`csa_session`), token SHA-256 hashes, user agent, IP tracking, idle/absolute expirations, and revocation timestamps.
- **`app.invites`**: Single-use cryptographic invite and password-reset tokens, role grants, optional `client_id` association, and expiration tracking.
- **`app.audit_log`**: Immutable audit records tracking every privileged operation (`action`, `actor_user_id`, `target_type`, `target_id`, `ip`, and structured `details` JSONB).
- **`app.login_attempts`**: Rate-limiting records tracking login successes and failures per email and IP address.
- **`app.projects`**: Internal and tenant-scoped projects (`name`, `slug`, `description`, `goals`, `status`: `setup`, `active`, `archived`, `scope`: `client`, `internal`, `row_version`: optimistic locking integer, `client_id`: nullable soft reference to `clients.id`, `created_by`, `created_at`, `updated_at`). Constraint enforces client projects have `client_id` and internal projects have `NULL`. Partial unique index on `slug` for internal scope.
- **`app.project_channels`**: Mappings between projects and channel endpoints (`webhook`, `telegram`, `whatsapp`, `gmail`) using unique `ref` keys (e.g. webhook `public_id` or Telegram chat ID).
- **`app.faq_files`**: Metadata records for knowledge base documents (`name`, `stored_as`, `folder`, `mime_type`, `pages`, `uploaded_by`).
- **`app.pm_milestones`**: Project milestones (`project_id`, `title`, `description`, `due_at`, `status`: `open`, `completed`, `cancelled`, `acceptance_criteria`: JSONB list, `position`, `version`, `created_by`, timestamps).
- **`app.pm_tasks`**: Tasks within projects (`project_id`, `milestone_id`, `title`, `description`, `status`: `backlog`, `ready`, `in_progress`, `review`, `done`, `cancelled`, `priority`: `low`, `medium`, `high`, `urgent`, `human_assignee_id`: FK to `app.users(id)`, `agent_assignment_id`: nullable string for future worker runs, `acceptance_criteria`: JSONB list with checklist items, `labels`: text array, `position`: column-relative ordering index, `version`: optimistic locking integer, `override_reason`: mandatory string when moving blocked/incomplete tasks, `cancellation_reason`: mandatory string when cancelling, `created_by`, timestamps).
- **`app.pm_task_dependencies`**: Directed predecessor/successor dependencies (`project_id`, `task_id`, `depends_on_task_id`, `created_at`). Enforces cycle avoidance and project scope consistency.
- **`app.pm_task_comments`**: Task discussion timeline (`project_id`, `task_id`, `author_user_id`: FK to `app.users(id)`, `author_assignment_id`, `body`, `visibility`: `admin_only`, `client_visible`, timestamps).
- **`app.pm_notes` & `app.pm_note_revisions`**: Project knowledge notes with markdown body, folder trees, full-text search, bidirectional task links, and immutable revision history.
- **`app.pm_agent_definitions` & `app.pm_agent_definition_versions`**: Agent catalog and prompt schemas for planning and specialist roles (`system_prompt`, `default_model`, `temperature`, `version_number`).
- **`app.pm_runs` & `app.pm_jobs`**: Durable AI planning execution records and transactional job queue leases (`status`: `queued`, `running`, `waiting_input`, `waiting_review`, `succeeded`, `failed`, `cancelled`), monotonic fencing tokens, and heartbeat timestamps.
- **`app.pm_run_events`**: Monotonic event streams (`seq`, `event_type`, `payload`) supporting live SSE streaming with `Last-Event-ID` cursor recovery.
- **`app.pm_proposals`**: Structured candidate plans (`revision_number`, `proposal_hash`, `structured_body` JSONB, `acceptance_map` JSONB, `status`: `draft`, `accepted`, `rejected`, `superseded`).
- **`app.pm_checkpoints`**: Checkpointing state store for graph resumes and human-in-the-loop approvals.
- **`app.pm_artifacts`**: Architecture, diagram, and ADR design entities (`project_id`, `kind`: `diagram`, `architecture`, `adr`, `patch`, `title`, `description`, `current_revision_id`, `row_version`: optimistic locking integer, `created_by`, timestamps).
- **`app.pm_artifact_revisions`**: Immutable revision history for diagrams and topologies (`project_id`, `artifact_id`, `revision_number`, `format`: `mermaid`, `plantuml`, `architecture_json`, `diagram_type`, `source_text`, `source_json`, `content_hash`: SHA-256, `validation_status`: `valid`, `invalid`, `validation_errors`: JSONB list, `assumptions`: text array, `evidence`: text array, `trust_boundaries`: text array, `failure_modes`: text array, `adr_content`, `created_by`, timestamps).
- **`app.pm_artifact_exports`**: Export records and sanitized SVGs (`project_id`, `artifact_id`, `revision_id`, `export_format`: `svg`, `content_svg`: sanitized SVG payload, `content_hash`: SHA-256, `created_by`, timestamps).
- **`app.pm_repositories`**: Project Git remotes with protocol allowlisting (`https://`, `ssh://`, `git@`), credential references, and SSRF/private IP protection.
- **`app.pm_workspaces`**: Server-owned directory locators for human terminals (`human_terminal`) vs autonomous agent coding runs (`agent_run`), path traversal validation, and status tracking.
- **`app.pm_terminal_sessions`**: Stateful PTY session records with project and workspace scoping, owner verification, runner PTY ID, 24-hour expiration, and state tracking (`active`, `detached`, `terminated`, `expired`).
- **`app.pm_terminal_tickets`**: Cryptographic single-use attach tickets (TTL 60s) with SHA-256 storage, scoped to user, session, and allowed origin.
- **`app.pm_investigations`**: Evidence-based bug investigations dispatched to Hermes (`project_id`, `task_id`, `run_id`, `workspace_id`, `external_job_id`, `title`, `status`: `queued`, `running`, `reproduced`, `suspected`, `inconclusive`, `failed`, `cancelled`, `symptom`, `reproduction_command`, `observed_result`, `expected_result`, `confidence`: `low`, `medium`, `high`, `evidence_locations`: JSONB list of file citations & line ranges, `suspected_causes`: text array, `proposed_fix`, `patch_artifact_id`, `missing_info`: text array, `telemetry`: JSONB, timestamps).
- **`app.pm_workroom_runs`**: Durable multi-agent workroom runs (`project_id`, `task_id`, `workspace_id`, `title`, `workflow_type`: `bug_fixture`, `feature_implementation`, `architecture_design`, `status`: `pending`, `running`, `paused`, `review_ready`, `patch_proposed`, `tests_passed_sandbox`, `completed`, `failed`, `cancelled`, `budget_exceeded`, `current_stage`, `active_role`: `planner`, `architect`, `investigator`, `implementer`, `reviewer`, `qa`, `repair_cycles`: integer count up to 2, `max_repair_cycles`: default 2, `budgets`: JSONB limits for wall time, tokens, cost, `usage`: JSONB aggregate tokens, seconds, cost, `review_bundle`: JSONB synthesis for operator signoff, `created_by`, timestamps).
- **`app.pm_workroom_messages`**: Strongly-typed inter-agent collaboration messages with strict tenant/project isolation (`run_id`, `project_id`, `from_role`, `to_role`, `message_type`: `proposal`, `question`, `evidence`, `handoff`, `review`, `final`, `content`, `metadata`: JSONB containing line citations, code diffs, test logs, repair counts, `created_at`).
- **Seeded Agent Roster**: Seeded global definitions and immutable version 1 records in `app.pm_agent_definitions` and `app.pm_agent_definition_versions` for all 6 discrete roles: `planner`, `architect`, `investigator`, `implementer`, `reviewer`, and `qa`.

### 3.2 Domain Database (Remote `csa_db`, Port 5433 via SSH)
Existing live database containing CSA production operational data:
- **`capability_registry`**: Agent catalog metadata (`name`, `display_name`, `tier`, `workflow_id`, `circuit_state`, `fail_count`, `requires_human`, `is_enabled`, `client_enabled`, `client_enabled_changed_at`, `client_enabled_changed_by`).
- **`agent_runs`**: Historical telemetry of every agent execution (`trace_id`, `agent`, `ok`, `latency_ms`, `error_code`, `error_category`, `input`, `output`, `created_at`).
- **`clients`**: Master customer/tenant registry (`id`, `name`, `slug`, `tier`, `status`).
- **`conversations` & `messages`**: Multi-turn dialogue history across channels, including `web_console`.
- **`webhook_endpoints`**: Channel webhook endpoint definitions (`public_id`, `name`, `is_enabled`).

### 3.3 Engine Separation & Security Constraints
1. **`app_engine`**: Full read/write connection pool (`pool_size=5`) to local `APP_DATABASE_URL`.
2. **`domain_engine`**: Session-level enforced read-only connection pool (`options="-c default_transaction_read_only=on -c statement_timeout=8000"`) connecting to `DOMAIN_DATABASE_URL` via `csa_console_ro`.
3. **`domain_write_engine`**: Dedicated, narrow write engine (`pool_size=2`, `options="-c statement_timeout=5000 -c lock_timeout=2000"`) for tenant agent switches (`PUT /api/client/agents/{name}/enabled`) and conversation deletion (`DELETE /api/chat/history/{id}`). Shares port 5433 with column-level updates granted on `capability_registry(client_enabled, client_enabled_changed_at, client_enabled_changed_by)` and `conversations(status, closed_at)`.

---

## 4. Backend Implementation (FastAPI)

### 4.1 Application Lifecycle & Middlewares (`app/main.py`)
- **Lifespan Manager**: On startup, launches an asynchronous background Uvicorn server running the dedicated voice listener (`app.modules.voice.server:voice_app`) on `0.0.0.0:8090`. Gracefully shuts down on app termination. Skipped automatically in test runs (`PYTEST_CURRENT_TEST`).
- **CSRF Origin Check Middleware**: Validates `Origin` header against `CONSOLE_ORIGIN` for all state-changing HTTP methods (`POST`, `PUT`, `PATCH`, `DELETE`).
- **Cache-Control Middleware**: Enforces `Cache-Control: no-store` on all `/api/*` responses to prevent client-side credential and data caching.
- **Global Health Check (`/api/health`)**: Probes database connectivity for both `app_db` and `domain_db`.

### 4.2 Core Services & Security (`app/core`)
- **Authentication (`deps.py`, `sessions.py`)**: Resolves `CurrentUser` from secure cookie `csa_session`. Enforces idle timeouts (24h) and absolute validity (14d). Role-based guard dependencies include `require_admin`, `require_client`, and `require_client_owner`.
- **Password & Token Security (`security.py`)**: Argon2id password hashing with timing attack mitigations via dummy hash evaluation for unknown users. Password complexity validation (minimum 12 chars, dictionary checks, email username exclusion).
- **Brute-Force Rate Limiting (`ratelimit.py`)**: Maximum 5 failures per email or 20 failures per IP within 15 minutes.
- **Sanitization & PII Masking (`sanitize.py`)**: Recursively sanitizes data before leaving the API. Strips sensitive keys (`body_raw`, `pii_map`), redacts secret fields (`[hidden]`), and masks emails, Malaysian NRICs, phone numbers, and payment cards.

### 4.3 Functional Modules (`app/modules`)

#### A. Authentication (`app/modules/auth`)
- `POST /api/auth/login`: Authenticates credentials with rate limiting and sets HttpOnly session cookie.
- `POST /api/auth/logout`: Revokes active session and clears browser cookie.
- `GET /api/auth/me`: Returns identity, role, and password change requirement.
- `POST /api/auth/password/change`: Updates password, clears forced-change flag, and revokes other active sessions.

#### B. Team & User Administration (`app/modules/team`)
- `POST /api/admin/invites`: Generates one-time invite links for new platform administrators.
- `GET /api/admin/invites` & `DELETE /api/admin/invites/{id}`: Lists and revokes pending admin invitations.
- `GET /api/admin/users`: Lists platform administrators with active session counts.
- `POST /api/admin/users/{id}/disable` & `/enable`: Toggles admin status (prevents disabling the last active admin).
- `POST /api/admin/users/{id}/revoke-sessions`: Terminates all active sessions for a target user.
- `POST /api/admin/users/{id}/reset-link`: Issues one-time password reset links.
- `GET /api/admin/audit`: Paginated immutable audit trail with actor, target, and IP metadata.
- `GET /api/invites/{token}` & `POST /api/invites/{token}/accept`: Public invite resolution and atomic acceptance.

#### C. Client Onboarding & Project Management (`app/modules/admin_clients` & `app/modules/client`)
- `GET /api/admin/clients`: Lists all tenants from domain DB enriched with local project and user counts.
- `POST /api/admin/clients/{id}/invites`: Invites tenant owners (`client_owner`) or members (`client_member`).
- `GET /api/admin/clients/{id}/users`: Lists users associated with a specific tenant.
- `GET /api/admin/clients/{id}/projects` & `POST /api/admin/clients/{id}/projects`: Manages tenant projects.
- `GET /api/admin/clients/{id}/channel-hints`: Inspects existing conversation thread keys and webhooks for binding.
- `POST /api/admin/clients/{id}/projects/{pid}/channels`: Binds communication channels to projects.
- `GET /api/client/profile`: Fetches tenant business name, plan tier, and current user role.
- `GET /api/client/projects`, `POST`, `PATCH`: Tenant self-service project management.
- `POST /api/client/projects/{pid}/channels`, `DELETE`: Tenant channel binding to available webhook endpoints.
- `GET /api/client/team`: Lists tenant members and invites (accessible to `client_owner`).
- `POST /api/client/team/invites`, `DELETE`: Manages tenant member invitations.
- `POST /api/client/team/users/{id}/disable`: Disables member accounts and revokes active sessions.

#### D. Project Management (PM) Hub (`app/modules/pm`)
- `GET /api/admin/pm/projects`: Cursor-paginated projects with scope/status/search filters and domain client metadata enrichment.
- `POST /api/admin/pm/projects`: Creates internal or client project with domain validation, slug collision avoidance, and audit logging (`project.created`).
- `GET /api/admin/pm/projects/{id}`: Detailed workspace metadata with connected channels and client profile.
- `PATCH /api/admin/pm/projects/{id}`: Concurrency-controlled updates via `row_version` (HTTP 409 conflict detection) with audit logging (`project.updated`). Frees channel bindings on archive.
- `GET /api/admin/pm/projects/{id}/milestones`: Milestones list with dynamically aggregated task count and completion ratio.
- `POST /api/admin/pm/projects/{id}/milestones`: Creates project milestone (`pm.milestone.created`).
- `PATCH /api/admin/pm/projects/{id}/milestones/{mid}`: Updates milestone metadata with optimistic concurrency (`pm.milestone.updated`).
- `DELETE /api/admin/pm/projects/{id}/milestones/{mid}`: Deletes milestone, safely unlinking associated tasks (`pm.milestone.deleted`).
- `GET /api/admin/pm/projects/{id}/assignees`: Lists eligible assignee users (`platform_admin` and client team members).
- `GET /api/admin/pm/projects/{id}/tasks`: Cursor-paginated task query supporting status, milestone, assignee, and text search filters.
- `POST /api/admin/pm/projects/{id}/tasks`: Creates a new task with automatic column positioning (`pm.task.created`).
- `GET /api/admin/pm/projects/{id}/tasks/{tid}`: Detailed task inspect view with predecessor/successor dependency graphs and calculated `is_blocked` flag.
- `PATCH /api/admin/pm/projects/{id}/tasks/{tid}`: Updates task fields with optimistic concurrency version check (`pm.task.updated`).
- `POST /api/admin/pm/projects/{id}/tasks/{tid}/move`: Atomic Kanban move and reordering with dependency checks, criteria completion validation, and optimistic concurrency version conflict (HTTP 409) handling (`pm.task.moved`).
- `POST /api/admin/pm/projects/{id}/tasks/{tid}/dependencies`: Adds task dependency with transactional cycle rejection via PostgreSQL advisory locks (`pm.task.dependency_added`).
- `DELETE /api/admin/pm/projects/{id}/tasks/{tid}/dependencies/{dep_id}`: Removes task dependency (`pm.task.dependency_removed`).
- `GET /api/admin/pm/projects/{id}/tasks/{tid}/comments`: Lists chronological task comments.
- `POST /api/admin/pm/projects/{id}/tasks/{tid}/comments`: Adds task comments (`pm.task.comment_created`).
- `GET /api/admin/pm/projects/{id}/activity`: Project activity stream with cursor pagination projected from `app.audit_log`.

#### E. Agent Health & Monitoring (`app/modules/agents` & `app/modules/client`)
- **Health Model v2 (`health.py`)**: Computes responsive health states based on immediate run patterns:
  - `disabled`: Provider disabled (`is_enabled = False`).
  - `client_off`: Tenant turned off (`client_enabled = False`).
  - `circuit_open`: Circuit breaker open in registry.
  - `workflow_off`: n8n workflow inactive.
  - `failing`: Last 3 consecutive runs failed.
  - `degraded`: >= 40% failure in last 5 runs.
  - `recovering`: Recent runs passing, but 24-hour success rate below 90%.
  - `idle`: No executions in the past 7 days.
  - `healthy`: Passing normal operations.
- **Admin Monitoring (`agents/router.py`)**:
  - `GET /api/admin/system/overview`: High-level service connectivity probes (Coordinator, Context registry, Adapters, Ingresses, Egresses).
  - `GET /api/admin/agents/overview`: Global matrix of all agents across clients with 24h/7d metrics, p95 latency, and last failure indicators.
  - `GET /api/admin/agents/{name}`: Detailed single-agent view with 14-day daily trends and 7-day error breakdowns.
  - `GET /api/admin/agents/{name}/runs`: Paginated execution logs filterable by status and error codes.
  - `GET /api/admin/agent-runs/{id}`: Detailed trace view with full PII sanitization.
- **Client Monitoring (`client/router.py`)**:
  - `GET /api/client/agents`: Friendly tenant agent catalog (`AGENT_COPY`), status mapping (`working`, `quiet`, `needs_attention`, `off`, `paused`), and project-filtered metrics.
  - `PUT /api/client/agents/{name}/enabled`: Tenant agent toggle. Enforces tenant scope, rejects required agents (`escalation_agent`), audits actions, and updates `client_enabled`.
  - `GET /api/client/activity`: Live activity feed with cursor-based pagination.

#### F. n8n Live Executions Monitor (`app/modules/agents/n8n.py`)
- `GET /api/admin/system/executions`: Queries n8n API (`/api/v1/executions` and `/api/v1/workflows`) with a 5-second in-memory cache. Resolves workflow IDs to human names, calculates run durations, and constructs browser-direct URLs via `N8N_PUBLIC_URL`.

#### G. Knowledge Base (FAQ) Management (`app/modules/client/router.py`)
- `GET /api/client/faq`: Lists uploaded documents for the authenticated client.
- `POST /api/client/faq`: Multipart file upload with strict validation:
  - File name safety checks (prevents path traversal characters).
  - Extension allowlist (`.pdf`, `.docx`).
  - Max size limit of 10 MB.
  - Magic byte validation (`%PDF-` for PDF, `PK` for DOCX).
  - Encodes file to Base64 and posts schema 2.0 payload to `FILES_WEBHOOK_URL` (`csa-console-files`).
  - Records metadata in `app.faq_files` and saves local disk copy under `data/faq/<client_id>/`.
- `GET /api/client/faq/{filename}`: Securely streams file content from local disk.
- `DELETE /api/client/faq/{filename}`: Two-phase deletion:
  - Looks up stored location (`stored_as` and `folder`) from `app.faq_files`.
  - Sends a `POST` request with `{ "kind": "delete_file", "file": { "stored_as", "folder" } }` to `FILES_WEBHOOK_URL`.
  - Requires upstream confirmation (`HTTP 200` with `{ "ok": true }`) before deleting the local file and database record.

#### H. Multi-Modal Chat & Turn Polling (`app/modules/chat/router.py`)
- `POST /api/chat/send`: Ingests text and/or file attachments (up to 3 files, 40 MB total).
  - Validates conversation ownership against `conversations` table.
  - Forwards documents to `FILES_WEBHOOK_URL`.
  - Forwards messages to `CHAT_WEBHOOK_URL` (`csa-console-chat`) with `X-CSA-Chat-Trace-ID` and ingress authentication headers.
  - Returns immediate response or a 504 timeout instructing client to poll.
- `GET /api/chat/poll`: Queries turn status using `kind: "poll"` with matching `trace_id` and `idempotency_key`.
- `GET /api/chat/history`: Retrieves recent conversations from domain database (filters out conversations where `status = 'deleted'`).
- `GET /api/chat/history/{id}`: Loads ordered message history with attachment metadata (returns 404 if not found or if `status = 'deleted'`).
- `DELETE /api/chat/history/{id}`: Soft delete of conversation in domain database via `domain_write_engine` (sets `status = 'deleted'`, `closed_at = now()`) with audit logging in `app.audit_log`, avoiding foreign key conflicts with tickets, pending actions, and voice transcripts.

#### I. Authenticated Voice Audio Server (`app/modules/voice`)
- **Server (`server.py`)**: Standalone FastAPI application (`voice_app`) running on port 8090.
  - `GET /health`: Health probe returning `{"ok": true}`.
  - `GET` & `HEAD /internal/voice/{file_id}.{ext}`: Streams stored audio recordings with signed URL verification.
  - Security validation:
    - `file_id` must be a valid UUID.
    - Extension must be in allowlist (`webm`, `ogg`, `oga`, `mp3`, `m4a`, `mp4`, `wav`).
    - Path traversal prevention: enforces target file resolves within `VOICE_TMP_DIR`.
    - HMAC signature & expiration validation: checks HMAC-SHA256 signature against `VOICE_URL_SIGNING_SECRET` and rejects expired requests (HTTP 410 Gone).
    - `AudioFetchLoggingMiddleware`: Logs request path, status code, bytes sent, and remote client IP.
    - Response headers include `Accept-Ranges: bytes` and `Cache-Control: no-store`.
- **Signing Utilities (`signing.py`)**:
  - `sign_voice_url`: Computes HMAC-SHA256 over `<file_id>.<ext>:<exp>`.
  - `verify_voice_sig`: Timing-safe comparison using `hmac.compare_digest`.
  - `build_audio_uri`: Assembles URL with `exp` and `sig` query parameters.
- **Voice Submission (`POST /api/voice`)**:
  - Accepts multipart audio from browser client (`UploadFile`).
  - Validates MIME type and size (500 B to 10 MB).
  - Persists file to `VOICE_TMP_DIR/<file_id>.<ext>`.
  - Computes signed URL valid for 10 minutes (`exp = time + 600`).
  - Dispatches `kind: "voice"` envelope with `audio_uri` to `CHAT_WEBHOOK_URL`.
  - Returns `{"status": "transcribing", ...}` for the client to poll.
- **Verification Tool (`check.py`)**:
  - Invoked via `make check-voice`. Validates listener health and sample HMAC generation/fetch.

---

## 5. Frontend Implementation (Next.js 16)

### 5.1 Architecture & Design System
- **Framework**: Next.js 16 (App Router), React 19, TypeScript.
- **Styling**: Tailwind CSS v4 using modern `@theme` token definitions and custom utility classes (`globals.css`).
- **Aesthetic**: Premium light blue and icy blue design system (#F4F7FE canvas, #2563EB royal blue accents, deep navy typography, glassmorphic card containers, and micro-animations).
- **Typography**: Inter (primary sans-serif) and JetBrains Mono (monospaced metrics/code) loaded via `next/font`.
- **Component Kit (`components/ui/kit.tsx`)**: Unified component primitives including `PageHeader`, `Card`, `StatCard`, `Banner`, `EmptyState`, `LiveIndicator`, `Tabs`, `Switch`, `Pill`, `Avatar`, `OneTimeLink`, `Drawer`, and loading skeletons.
- **Layout Shell (`components/AppShell.tsx`)**:
  - Collapsible dark sidebar with responsive mobile drawer support.
  - Top glass header with dynamic breadcrumbs and user profile menu.
  - Global Command Palette (`⌘K`) for fast keyboard-driven navigation across admin and portal sections.
  - Client state persisted via `localStorage` and `useSyncExternalStore`.
- **Auth Guard & Proxying**:
  - `proxy.ts`: Edge rewrite/redirect checking `csa_session` cookie; redirects unauthenticated users to `/login`.
  - `next.config.ts`: Internal rewrite proxying `/api/:path*` directly to FastAPI on `http://127.0.0.1:8100/api/:path*`.

### 5.2 Application Route Map

#### Authentication (`app/(auth)`)
- `/login`: Split-screen login interface with email/password authentication, password visibility toggle, and error handling.
- `/invite/[token]`: Public acceptance page for administrative or tenant invitations.
- `/change-password`: Forced password change interface featuring interactive password strength meters and validation.

#### Platform Administration (`app/(admin)`)
- `/`: Platform command center displaying live operational KPIs, agents requiring attention, and latest workflow executions.
- `/projects`: Admin project hub with scope filters (All/Client/Internal), lifecycle status filters, search, metrics, card grid, and creation drawer.
- `/projects/[id]`: Project workspace hub with overview attributes, goals, description, channel bindings list with links to client settings, panel navigation tabs, and edit drawer with conflict handling.
- `/agents`: Global multi-tenant agent health grid with client filtering, state chips, and 5-second polling.
- `/agents/[name]`: Drill-down view into specific agent performance, 14-day trends, error category breakdown, and sanitized execution traces.
- `/system`: Real-time n8n workflow execution monitor with 5s polling, status filter tabs (Success, Error, Running), search, duration formatting, and direct deep links to execution details in n8n.
- `/settings/clients`: Master-detail workspace for tenant onboarding, client user management, project creation, and channel binding.
- `/settings/team`: Platform admin team management, one-time invite generation, account suspension, session revocation, and password resets.
- `/settings/audit`: Infinite-scrolling immutable audit log with real-time text filtering and tone-coded action tags.

#### Client Portal (`app/portal`)
- `/portal`: Client workspace overview with active project cards, tenant agent grid, and live activity stream.
- `/portal/chat`: Conversational AI interface supporting:
  - Multi-turn conversation sidebar with thread history, new chat creation, and conversation deletion with inline confirmation.
  - Full markdown formatting, syntax highlighting, and response copy actions.
  - Drag-and-drop document attachments (.pdf, .docx) with chip indicators.
  - Integrated voice note recorder (`MediaRecorder` with Opus/WebM/MP4 support, live timer, level meter, cancel/confirm, and 120s auto-stop).
  - Real-time turn polling (`transcribing` shimmer -> `processing` typing indicator -> `done` with markdown response).
  - In-flight turn recovery using `localStorage` (`csa-console-chat-pending`).
- `/portal/agents`: Tenant agent catalog with descriptions, operational states (`working`, `quiet`, `needs_attention`, `off`, `paused`), and interactive toggles for optional capabilities.
- `/portal/projects` & `/portal/projects/[id]`: Project workspace allowing clients to group business channels.
- `/portal/faq`: Knowledge base document upload with drag-and-drop zones, animated upload feedback, and homeserver-synchronized document deletion.
- `/portal/team`: Account team management allowing tenant owners to invite members or revoke access.

---

## 6. Development Workflow & Operational Runbook

### 6.1 Makefile Commands (Root & Subdirectory)
The repository includes standard Makefiles at the root (`WebConsoleCSA/Makefile`) and in `csa-console/Makefile`:
```bash
make dev          # Concurrently starts Next.js (:3001) and FastAPI (:8100)
make tunnel       # Establishes SSH port forwards (5433, 5679, 8001) to nexusserver
make check-voice  # Runs CLI verification of voice server health and signed URL retrieval
make web          # Starts Next.js frontend only (PORT=3001)
make api          # Starts FastAPI backend only (port 8100 with reload)
make db-up        # Starts local Postgres Docker container (console_db on 5440)
make db-down      # Stops local Postgres container
make install      # Installs both web (npm install) and api (uv sync) dependencies
make lint         # Runs ESLint on web and Ruff check on API
```

### 6.2 Verification & Test Suites
- **Backend Tests (`csa-console/api/tests`)**: 86 automated tests passing via `uv run pytest`:
  - `test_pm_tasks.py`: Task management, Kanban, milestones, and dependencies suite (17 tests) covering `pm_enabled` fail-closed flag, project access authorization, milestone lifecycle & task aggregation, column status transitions (`backlog → ready → in_progress → review → done`), dependency cycle detection via PostgreSQL transaction advisory locks, blocked status enforcement, acceptance criteria completion gates with authorized override reasons, atomic reordering, optimistic concurrency version conflicts (HTTP 409), task comments, and project activity feed projection from `app.audit_log`.
  - `test_pm.py`: Project management hub suite (14 tests) covering `PM_ENABLED` fail-closed feature flag (404), access resolver permissions, tenant isolation against internal projects, slug collision avoidance, domain client validation dependencies (503 on database outage), optimistic concurrency conflict detection (409), and privileged write audit logging.
  - `test_chat_delete.py`: Soft deletion via `status = 'deleted'`, history filtering, cross-tenant isolation (403), non-existent handling (404), and audit logging.
  - `test_agent_toggle.py`: Tenant scoping, toggle permissions, audit logging, and state resolution.
  - `test_client.py`: Client API endpoints, stats queries, and trend calculations.
  - `test_core.py`: Authentication, sessions, rate limiting, and password policies.
  - `test_voice.py`: Voice health endpoint, HMAC URL signing, signature validation, expiration (HTTP 410), tamper detection, path traversal prevention, and audio streaming headers.
- **Frontend Quality**: ESLint passes with zero errors (`npm run lint`). Next.js production builds compile cleanly (`npm run build`).

### 6.3 Known Operational Notes & Constraints
1. **Running `make check-voice`**: Requires the backend API to be running first (e.g. via `make dev` or `make api`), because the voice server is started as a lifespan task within the FastAPI process.
2. **SSH Tunnel Dependency**: Features relying on live domain data (`agent_runs`, `capability_registry`, conversations), n8n workflow executions, and coordinator health require the SSH tunnel (`make tunnel`) to be active.
3. **Voice Audio Remote Fetching**: When remote n8n instances need to transcribe recorded audio, `VOICE_AUDIO_BASE_URL` must resolve to the developer's laptop over the Tailscale network (e.g. `http://<tailscale-machine-name>.tail4c769f.ts.net:8090/internal/voice`).
4. **Agent Toggle Guard**: The `PUT /api/client/agents/{name}/enabled` route modifies the remote `csa_db`. When running in dev-live, ensure testing is restricted to sandbox clients.
5. **Conversation Deletion Permissions**: Soft deletion (`DELETE /api/chat/history/{id}`) executes an `UPDATE` on `conversations(status, closed_at)` in `csa_db` via `domain_write_engine`. Requires `GRANT UPDATE (status, closed_at) ON conversations TO csa_console_ro;` on the live database.

---

## 7. Agentic Project Management Track

### 7.1 Phase 00: Verified Discovery
- **Status**: Complete (`implemented_local`).
- **Deliverables**: Historical repository findings, implementation log and decisions; the old `Docs/` pack was removed on 2026-10-20.

### 7.2 Phase 01: Admin Project Hub and Internal Projects
- **Status**: Complete (`implemented_local`). Additive schema migration applied to local `console_db` (`app.projects`), feature flag enabled (`PM_ENABLED=true` in `api/.env`), and end-to-end project creation and retrieval smoke-tested.
- **Backend Architecture & Routes**:
  - `PM_ENABLED=true` activated in development `.env` alongside roadmap flags (`PM_AI_ENABLED`, `PM_RUNNER_ENABLED`, `PM_TERMINAL_ENABLED`, `PM_HERMES_ENABLED`, `PM_CLIENT_PORTAL_ENABLED`) defined in `Settings` (`app/config.py`).
  - Route fail-closed guard `require_pm_enabled` added to `app/core/deps.py` (fails closed with 404 when flag is disabled).
  - Admin PM module implemented at `app/modules/pm/`:
    - `service.py`: `resolve_project_access`, `list_projects` (with cursor pagination and domain client enrichment), `get_project_detail`, `create_project` (with slug collision avoidance and domain validation), and `update_project` (with optimistic concurrency row_version checking).
    - `router.py`: Registered at `/api/admin/pm` (`GET /projects`, `POST /projects`, `GET /projects/{id}`, `PATCH /projects/{id}`).
  - Tenant query isolation & metrics:
    - Updated `app/modules/admin_clients/router.py` to filter `where client_id is not null` when counting tenant projects.
    - Tenant API (`POST /api/client/projects`) strictly strips/ignores arbitrary scopes, enforcing tenant client ID.
- **Frontend Architecture & Pages**:
  - Extended `lib/api.ts` with `patch<T>` and `del<T>`.
  - Added "Projects" navigation item to `ADMIN_NAV` in `components/AppShell.tsx` and integrated with `⌘K` command palette.
  - `/projects`: Admin Project Hub featuring scope filter (All/Client/Internal), status filter, live search, summary stat cards, project card grid, and Create Project drawer with domain client validation.
  - `/projects/[id]`: Project workspace hub featuring project attributes, goals, description, channel bindings list with links to client settings, panel navigation tabs, and Edit Project drawer with optimistic version conflict resolution (409).
- **Test Evidence**:
  - Automated tests: 69/69 passing (`uv run pytest` in 1.64s), including 14 new comprehensive unit tests in `tests/test_pm.py` covering fail-closed flags, access resolvers, internal/client scoping, slug uniqueness, domain DB lookup dependencies, and optimistic version conflict detection.
  - Frontend Quality: ESLint passing with zero errors (`npm run lint`), Next.js 16 production build compiling cleanly (`npm run build`).

### 7.10 Phase 09: Client Project Portal (Local Implementation)
- **Status**: Implemented locally; migration and feature flags are not applied/enabled by this change.
- **Sharing model**: Preserves the portal's existing tenant-wide access to legacy projects. PM project sharing is a separate opt-in projection available only for client-scoped projects. Admins explicitly select milestones, tasks, note revisions, and artifact revisions and author a client-facing summary. Every publish creates an immutable snapshot; later edits remain unpublished until republished, and unsharing revokes portal access to the active snapshot. Downloaded/copied content cannot be recalled.
- **Client permissions**: Client owners and members retain the existing tenant-wide portal rules. Both can view a shared PM snapshot and submit a question/change request. Requests are recorded for the admin project workspace and never start billable work or mutate plans. Terminal, agent runs, internal notes, prompts, tool arguments, repository credentials, and execution data are not projected.
- **Backend**: Added `app/modules/pm_client_portal.py` with admin publication/preview/unshare and request inbox routes plus tenant-scoped client snapshot and request routes. Client endpoints read publication snapshots only. Publish rejects internal projects and verifies every selected record belongs to the target project. All external mutations are audited.
- **Schema**: Added `api/sql/0011_pm_client_portal.sql` for immutable publication revisions and client requests. Operator must review and apply it before enabling `PM_CLIENT_PORTAL_ENABLED` (alongside `PM_ENABLED`).
- **Frontend**: Admin project overview includes selection, client preview, publish/revision, unshare, and client request inbox. Portal project list shows shared PM updates when the feature is enabled, with a client snapshot view and request form.
- **Validation**: No tests, lint, build, or database migration were run for this local implementation.

### 7.11 Phase 10: Production Hardening (Local Controls; Staging Gates Outstanding)
- **Status**: Local hardening implemented; not verified in staging or enabled as production.
- **Health/operations**: Kept `/api/health` compatible and added process liveness `/api/health/live`, dependency readiness `/api/health/ready`, and admin-only aggregate PM telemetry `/api/admin/ops/pm` (queue depth/age and worker heartbeat age). PM worker staleness and optional feature flags are reported separately from API database readiness.
- **Worker recovery and fencing**: Added optional durable LangGraph `PostgresSaver` using `PM_CHECKPOINT_DATABASE_URL`, with the supported library setup procedure. Development falls back to explicitly warned in-memory checkpoints. Production configuration rejects PM AI without the separate checkpoint URL. Workers persist idle/working heartbeats. Graph mutation and completion writes check current lease owner, unexpired lease and fencing state; expired leases cannot be revived by heartbeat. Proposal publication reuses an already committed proposal if the worker died before queue acknowledgement. Runs receive an `X-Request-ID` correlation ID persisted to the run and carried into worker lifecycle logs.
- **Production HTTP controls**: `DEPLOYMENT_ENVIRONMENT=production` requires HTTPS `CONSOLE_ORIGIN`, `COOKIE_SECURE=true`, and rejects missing or mismatched Origin on state-changing requests. Resumable SSE revalidates session revocation during delivery; terminal tickets bind to the auth session and WebSockets recheck user, session and terminal validity while connected. These settings are not enabled in development by default.
- **Schema**: Added `api/sql/0012_pm_operations.sql` for run correlation IDs, worker heartbeat records and terminal-ticket auth-session binding. Apply only after review and before updated workers.
- **Operator guidance**: The historical PM operations proposal was removed with the obsolete `Docs/` tree on 2026-10-20. Use maintained `docs/RUNBOOK.md` and `deploy/RELEASE.md` for current guarded procedures.
- **Unverified**: No tests/lint/build or staging drills were run. Production compose/supervisor, backup schedule/restore evidence, external artifact storage, checkpoint DB provisioning, ingress timeouts, capacity, on-call ownership and runner/Hermes network boundaries remain operator/staging work. Do not mark this phase `verified_staging` yet.

### 7.3 Phase 02: Tasks, Kanban, Milestones, and Activity
- **Status**: Complete (`implemented_local`). Additive schema migrations applied (`0004_pm_tasks.sql`), 17 comprehensive automated tests passing (`test_pm_tasks.py`), live FastAPI + PostgreSQL acceptance smoke test verified, Next.js 16 production build clean.
- **Database Architecture (`console_db`)**:
  - `app.pm_milestones`: Project milestone container with completion rollups.
  - `app.pm_tasks`: Tasks with 5-stage lifecycle (`backlog`, `ready`, `in_progress`, `review`, `done`, `cancelled`), optimistic concurrency `version`, priority, assignees, acceptance criteria, labels, and column positioning.
  - `app.pm_task_dependencies`: Predecessor/successor links with foreign keys, index cascades, and cycle avoidance.
  - `app.pm_task_comments`: Audit-ready task discussion notes.
- **Backend Architecture & Service (`app/modules/pm`)**:
  - `tasks_service.py`:
    - **Advisory-Locked Cycle Detection**: Serializes graph validation per project using PostgreSQL transaction advisory locks (`pg_advisory_xact_lock(hashtext(:p))`), running recursive CTE queries to prevent dependency cycles.
    - **Transition & Blocked Guards**: Enforces prerequisites before tasks can enter `ready` or `in_progress`. Unfinished predecessor tasks block movement unless an authorized `override_reason` is supplied. Blocked cards retain their active column while displaying a blocked status chip.
    - **Criteria Completion Gate**: Tasks cannot be moved to `done` while acceptance criteria items remain unchecked unless an `override_reason` is provided. Supports cancellation with mandatory `cancellation_reason`.
    - **Atomic Column Reordering**: Position shifting with contiguous renumbering on task move across columns.
    - **Curated Activity Feed**: Project-scoped stream derived directly from `app.audit_log` with human-readable event summaries.
  - `router.py`: Registered endpoints at `/api/admin/pm/projects/{id}` for `/milestones`, `/tasks`, `/assignees`, `/move`, `/dependencies`, `/comments`, and `/activity`. All mutations create structured entries in `app.audit_log`.
- **Frontend Architecture & Components (`web/components/pm`)**:
  - `TaskBoardView.tsx`: 5-column Kanban board (`Backlog`, `Ready`, `In Progress`, `Review`, `Done`) with drag-and-drop, accessible left/right move controls, List view toggle, real-time search, milestone/priority/assignee filters, and inline task creator. Optimistic state transitions roll back automatically on API error.
  - `TaskCard.tsx`: Priority badge, milestone label, blocked warning chip, acceptance criteria checklist counter, comment badge, assignee avatar, and move actions.
  - `TaskDrawer.tsx`: Sliding inspection drawer supporting detail updates, interactive acceptance criteria checklist, predecessor/successor dependency picker, comments thread, cancellation prompt, and override reason capture. Uses keyed inner content (`${task.id}-${task.version}`) for React 19 compliance without cascading state renders.
  - `MilestoneModal.tsx`: Milestone management with task progress bars, due dates, and acceptance criteria.
  - `ProjectActivityView.tsx`: Chronological timeline view of all project actions with actor avatars, timestamps, and metadata pills.
  - `app/(admin)/projects/[id]/page.tsx`: Activated `board` and `activity` tabs; integrated TaskBoardView, TaskDrawer, MilestoneModal, and ProjectActivityView.
- **Verification & Test Evidence**:
  - Automated tests: 86/86 passing (`uv run pytest`), including 17 new tests in `test_pm_tasks.py`.
  - Frontend Quality: ESLint passing with zero errors (`npm run lint`), Next.js 16 production build compiling cleanly (`npm run build`).
  - Live End-to-End Acceptance: Verified against running local PostgreSQL (`console_db`) with platform admin actor: created milestone and 5 tasks, linked dependencies, confirmed 400 cycle rejection, confirmed 400 blocked move rejection, confirmed 400 criteria gate rejection, advanced tasks through done, unblocked successors, posted comments, verified 409 version conflict detection, and confirmed activity feed projection.

### 7.4 Phase 03: Project Knowledge & Notes
- **Status**: Complete (`implemented_local`). Schema migration `0005_pm_notes.sql` applied. 16 automated tests passing in `tests/test_pm_notes.py`.
- **Database Architecture (`console_db`)**:
  - `app.pm_notes`: Knowledge notes with markdown body, folder path hierarchy, full-text tsvector search index, and metadata.
  - `app.pm_note_revisions`: Immutable historical revisions capturing changes over time.
  - `app.pm_task_note_links`: Bidirectional many-to-many associations between tasks and notes.
- **Backend Architecture & Service (`app/modules/pm`)**:
  - `notes_service.py`: CRUD, folder tree construction, PostgreSQL full-text search with highlighting snippets, and task-note association management.
- **Verification & Test Evidence**:
  - Automated tests: 102/102 backend tests passing.

### 7.5 Phase 04: Durable AI Planning and Reviewed Proposals
- **Status**: Complete (`implemented_local`). Additive schema migration `0006_pm_planner.sql` applied. 11 comprehensive automated tests passing in `tests/test_pm_planner.py` (113/113 total suite). Standalone worker daemon available via `make pm-worker`. Next.js 16 production build clean (`npm run build`).
- **Database Architecture (`console_db`)**:
  - `app.pm_agent_definitions` & `app.pm_agent_definition_versions`: Declarative agent catalog with versioned system prompts, model configs, and temperature. Default `pm_planner` seeded.
  - `app.pm_project_agent_assignments`: Project-level agent overrides and custom instructions.
  - `app.pm_runs`: Durable execution records tracking instruction, scoped context refs, token/wall-time/cost budgets, usage, and status (`queued`, `running`, `waiting_input`, `waiting_review`, `succeeded`, `failed`, `cancelled`).
  - `app.pm_jobs`: Transactional job queue with `SKIP LOCKED` atomic leases, monotonic fencing tokens (`fence`), heartbeats, and 45s lease TTLs.
  - `app.pm_run_events`: Monotonic sequence event stream (`seq`, `event_type`, `payload`) powering SSE updates.
  - `app.pm_proposals`: Candidate structured plans with SHA-256 canonical proposal hash, revision counter, and atomic acceptance map.
  - `app.pm_checkpoints`: LangGraph state store supporting execution pause, review resumption, and human-in-the-loop checkpoints.
- **Backend Architecture (`app/modules/pm_ai`)**:
  - `models.py`: Pydantic V2 models for `StructuredPlan`, `PlanTaskDraft`, `PlanMilestoneDraft`, and proposal schemas. Implements Kahn's algorithm for DAG topological sort and cycle rejection. Computes deterministic canonical proposal hashes.
  - `llm_provider.py`: `LLMProvider` protocol with `StubLLMProvider` for 100% offline deterministic test suites, with support for Anthropic (`claude-3-7-sonnet`) and OpenAI (`gpt-4o`).
  - `graph.py`: LangGraph state machine orchestrating 5 stages:
    1. `resolve_scoped_context`: Resolves project brief, goals, and scoped notes into bounded markdown evidence.
    2. `assess_missing_info`: Checks for incomplete specifications, pausing with `waiting_input` rather than hallucinating requirements.
    3. `draft_plan`: Synthesizes milestones, tasks, and acceptance criteria.
    4. `validate_structured_plan`: Validates DAG acyclicity and dependency existence, triggering a self-repair loop (capped at 2 attempts) on topological flaws.
    5. `publish_proposal`: Persists proposal row, transitions run to `waiting_review`, and awaits human decision.
  - `queue.py`: Atomic job enqueuing, idempotent replay, lease claims via `FOR UPDATE SKIP LOCKED`, monotonic fencing, and heartbeat refreshes.
  - `worker.py`: Standalone supervised worker daemon supporting continuous polling (`make pm-worker`) or single-job batch execution (`--once`).
  - `proposals_service.py`: Proposal detail queries, draft updates, dependency integrity validation, and atomic board acceptance:
    - Atomically maps proposal-local task IDs to database UUIDs.
    - Inserts milestones and tasks into `app.pm_milestones` and `app.pm_tasks`.
    - Handles idempotency replays and rejects conflicting duplicate acceptances (409).
    - Writes structured records to `app.audit_log`.
  - `events_service.py`: Resumable SSE event stream handler with `Last-Event-ID` cursor recovery.
  - `router.py`: API endpoints mounted under `/api/admin/pm`:
    - `POST /projects/{pid}/runs`: Enqueues new planning run (HTTP 202).
    - `GET /projects/{pid}/runs`: Lists project runs.
    - `GET /projects/{pid}/runs/{rid}`: Run status and proposal link.
    - `POST /projects/{pid}/runs/{rid}/cancel`: Cancels queued or running job.
    - `GET /projects/{pid}/runs/{rid}/events`: Resumable SSE event stream.
    - `GET /projects/{pid}/proposals/{id}`: Structured proposal inspection.
    - `PATCH /projects/{pid}/proposals/{id}`: Human edits to draft proposal.
    - `POST /projects/{pid}/proposals/{id}/accept`: Atomic board acceptance.
    - `GET /agent-definitions`: Agent catalog inspection.
- **Frontend Architecture (`web/components/pm/planner`)**:
  - `PlannerModal.tsx`: Directive prompt input, project goal checkbox, scoped note multi-selector, budget configurator, and run initiator.
  - `RunTimelineModal.tsx`: Live 5-stage progression indicator, SSE event stream listener, cancel run button, missing information alerts, and link to review proposal.
  - `ProposalReviewModal.tsx`: Proposal inspection with milestone accordion, task checkboxes, real-time dependency conflict detector (warning if a task is selected without its required predecessor), and atomic "Accept Selected into Board" button.
  - `app/(admin)/projects/[id]/page.tsx`: Added "Plan with AI" button in project header with status pills and modal integrations.
- **Verification & Test Evidence**:
  - Automated tests: 113/113 passing (`uv run pytest` in 1.53s), including 11 tests in `tests/test_pm_planner.py` covering fail-closed flags, atomic leasing & fencing, deterministic DAG generation, missing info pausing, self-repair loop, unrecoverable failure handling, atomic acceptance with mapped UUIDs, idempotency, dangling dependency rejection, cancellation, and SSE cursor replay.
  - Code Quality: Ruff check passing on all `app/modules/pm_ai` and `tests/test_pm_planner.py`.
  - Frontend Quality: ESLint passing with zero errors, Next.js 16 production build compiling cleanly (`npm run build`).
  - Live End-to-End Acceptance: Verified against running local PostgreSQL (`console_db`) with `PMWorker`: successfully enqueued planning run, processed via worker with lease token fencing, generated proposal with 3 tasks and 2 milestones, verified SHA-256 proposal hash, accepted proposal into live board, and confirmed milestone and task creation in `app.pm_milestones` and `app.pm_tasks`.

### 7.6 Phase 05: Diagram Studio and Application/AWS Architecture
- **Status**: Complete (`implemented_local`). Schema migration `0007_pm_diagrams.sql` applied. 8 comprehensive automated tests passing in `tests/test_pm_diagrams.py` (121/121 total test suite). Frontend Next.js 16 build passing cleanly (`npm run build`). ESLint passing with zero errors and warnings in PM components (`npm run lint`).
- **Database Architecture (`console_db`)**:
  - `app.pm_artifacts`: Design entities scoped to projects (`kind`: `diagram`, `architecture`, `adr`, `current_revision_id`, `row_version`).
  - `app.pm_artifact_revisions`: Immutable revision history for diagrams and topologies (`format`: `mermaid`, `plantuml`, `architecture_json`, `diagram_type`, `source_text`, `source_json`, `content_hash`: SHA-256, `validation_status`: `valid`, `invalid`, `validation_errors`: JSONB list, `assumptions`: text array, `evidence`: text array, `trust_boundaries`: text array, `failure_modes`: text array, `adr_content`, `created_by`, timestamps).
  - `app.pm_artifact_exports`: Export records and sanitized SVGs (`project_id`, `artifact_id`, `revision_id`, `export_format`: `svg`, `content_svg`: sanitized SVG payload, `content_hash`: SHA-256, `created_by`, timestamps).
- **Backend Architecture (`app/modules/pm_diagrams`)**:
  - `models.py`: Typed architecture models (`ArchitectureGraph`, `ArchitectureNode`, `ArchitectureBoundary`, `ArchitectureEdge`) with DAG boundary hierarchy cycle rejection and dangling edge validation. SHA-256 canonical hashing for version integrity.
  - `validator.py`: Safe parsing and strict SVG sanitization:
    - Mermaids: rejects dangerous directives (`%%{init:`, `<script>`, event handlers).
    - PlantUML: blocks unauthorized `!include` directives and arbitrary local/remote paths.
    - SVG sanitizer: strips `<script>`, `<foreignObject>`, inline `on*` event handlers, `javascript:` URLs, and external resource tags.
  - `architecture_service.py`: Standard production AWS architecture templates (3-Tier Web App with VPC/Subnet/ALB/ECS/RDS, Serverless REST API with API Gateway/Lambda/DynamoDB).
  - `diagrams_service.py`: Starter catalog registry (8 fixtures across sequence, architecture, C4 container, ERD, flowcharts, state diagrams), artifact/revision CRUD, optimistic locking, audit logging (`pm.artifact.create`, `pm.artifact.revision.create`), and SVG exports.
  - `generator.py`: AI diagram synthesis with deterministic offline stub mode and scoped project note grounding.
  - `router.py`: API endpoints mounted under `/api/admin/pm` (`/diagrams/catalog`, `/projects/{pid}/artifacts`, `/artifacts/{aid}/revisions`, `/artifacts/{aid}/export`, `/artifacts/generate`).
  - `config.py` & `deps.py`: Added `pm_diagrams_enabled: bool = True` and `require_pm_diagrams_enabled` dependency guard.
- **Frontend Architecture (`web/components/pm/diagrams`)**:
  - `MermaidViewer.tsx`: Safe client-side dynamic Mermaid rendering with strict securityLevel, syntax error banner, and sanitized SVG callback. Complies with React 19 rules (zero cascading setState in effect).
  - `ArchitectureCanvas.tsx`: Interactive zoomable/pannable canvas with nested boundary groupings (VPC, Subnet, AZ), AWS service category cards (Compute, Database, Storage, Networking, Security, Integration), flow edges, zoom/reset controls, and node inspector drawer.
  - `DiagramGenerateModal.tsx`: AI diagram generation modal with diagram type selector and scoped note grounding.
  - `DiagramStudio.tsx`: Split-screen source editor & visual preview, template launcher, revision history dropdown, SVG export, and Assumptions/Boundaries/ADR tabs. Keyed inner workspace prevents cascading state renders on revision change.
  - `app/(admin)/projects/[id]/page.tsx`: Activated `diagrams` tab, wired `DiagramStudio`, and updated roadmap card with live artifact counts.
- **Verification & Test Evidence**:
  - Automated tests: 121/121 passing (`uv run pytest` in 4.5s), including 8 tests in `tests/test_pm_diagrams.py` covering fail-closed flags, catalog, validation, sanitization, DAG architecture validation, CRUD revisions, SVG exports, and offline AI generation.
  - Code Quality: Ruff check passing on all `app/modules/pm_diagrams` and `tests/test_pm_diagrams.py`.
  - Frontend Quality: ESLint passing (`npm run lint`), Next.js 16 production build compiling cleanly (`npm run build`).
  - Live End-to-End Acceptance: Verified against running local PostgreSQL (`console_db`): catalog retrieval, artifact creation, revision appending, SVG export with script sanitization, offline AI generator synthesis with assumptions and ADR, and verified audit logging in `app.audit_log`.

### 7.7 Phase 06: Dedicated Project Workspaces and Terminal
- **Status**: Complete (`implemented_local`). Schema migration `0008_pm_terminal.sql` applied to local `console_db`. 8 comprehensive automated tests passing in `tests/test_pm_terminal.py` (129/129 total backend test suite). Frontend Next.js 16 build passing cleanly (`npm run build`). ESLint passing with zero errors (`npm run lint`). Full live DB and PTY lifecycle acceptance smoke-tested.
- **Database Architecture (`console_db`)**:
  - `app.pm_repositories`: Project Git remotes with protocol allowlisting (`https://`, `ssh://`, `git@`), credential references, and SSRF/private IP protection.
  - `app.pm_workspaces`: Server-owned directory locators for human terminals (`human_terminal`) vs autonomous agent coding runs (`agent_run`), path traversal validation, and status tracking.
  - `app.pm_terminal_sessions`: Stateful PTY session records with project and workspace scoping, owner verification, runner PTY ID, 24-hour expiration, and state tracking (`active`, `detached`, `terminated`, `expired`).
  - `app.pm_terminal_tickets`: Cryptographic single-use attach tickets (TTL 60s) with SHA-256 storage, scoped to user, session, and allowed origin.
- **Backend Architecture (`app/modules/pm_terminal`)**:
  - `repo_service.py`: Normalizes Git URLs, strips userinfo credentials, enforces protocol allowlist, blocks loopback/link-local/RFC1918 private IPs, and enforces uniqueness.
  - `runner.py`: Server-owned workspace path provisioning, strict environment variable sanitization (stripping app DB URLs, n8n keys, AI credentials, and SSH agent forwarding), and PTY child process management (`pty.openpty()`, `set_pty_winsize`, `terminate_pty_process`).
  - `pty_service.py`: Singleton `PTYManager` with asynchronous non-blocking read callbacks on the event loop, circular 100 KB scrollback buffer, client broadcast dispatch, resize event handler, and idle timeout termination.
  - `tickets.py`: Cryptographically secure single-use ticket generator (`secrets.token_urlsafe(32)`), SHA-256 hashing, and atomic consumption (`for update`).
  - `router.py`: REST endpoints mounted under `/api/admin/pm` (`/repositories`, `/workspaces`, `/terminal-sessions`, `/ticket`, `/terminate`, `/runner/status`) and authenticated WebSocket upgrade endpoint (`/terminal-sessions/{sid}/ws`) enforcing strict `Origin` header matching and single-use ticket verification.
  - `deps.py` & `config.py`: Added `pm_terminal_enabled: bool = False` (activated in dev `.env`), `pm_runner_enabled: bool = True`, runner workspace directories, and fail-closed dependency guard `require_pm_terminal_enabled`.
- **Frontend Architecture (`web/components/pm/terminal`)**:
  - `RunnerStatusBadge.tsx`: Real-time pill indicator displaying runner status (`Isolated Runner Active`, `Runner Degraded`, `Runner Offline`) and active PTY count.
  - `CreateSessionModal.tsx`: Launch modal allowing admins to select or auto-provision workspaces and title terminal tabs.
  - `XTermCanvas.tsx`: Dynamic client-side xterm.js terminal with FitAddon, custom dark theme (`#0A0E17`), JetBrains Mono typography, ResizeObserver synchronization, single-use ticket retrieval, and reconnect handling.
  - `TerminalWorkspace.tsx`: Multi-tab project terminal panel with tab switcher, detach support, terminate button, empty state, and sandboxing architecture badges.
  - `app/(admin)/projects/[id]/page.tsx`: Activated `terminal` tab, wired `TerminalWorkspace`, and updated roadmap checklist.
- **Verification & Test Evidence**:
  - Automated tests: 129/129 passing (`uv run pytest` in 2.66s), including 8 tests in `tests/test_pm_terminal.py` covering fail-closed flags, runner telemetry, repository URL SSRF & credential stripping, workspace path isolation, session lifecycle & single-use tickets, WebSocket origin rejection & attach, and cross-project isolation.
  - Code Quality: Ruff check passing on all `app/modules/pm_terminal` and `tests/test_pm_terminal.py`.
  - Frontend Quality: ESLint passing with 0 errors (`npm run lint`), Next.js 16 production build compiling cleanly (`npm run build`).
  - Live End-to-End Acceptance: Verified against running local PostgreSQL (`console_db`): runner health probe, repository registration, distinct workspace directory provisioning for Projects A and B, SSRF link-local rejection, single-use ticket verification & atomic consumption, replayed ticket rejection (401), and complete sanitization of runner environment variables.

### 7.8 Phase 07: Hermes Worker Integration & Evidence-Based Bug Investigation
- **Status**: Complete (`implemented_local`). Schema migration `0009_pm_investigations.sql` applied to local `console_db`. 7 comprehensive automated tests passing in `tests/test_pm_hermes.py` (136/136 total backend test suite). Frontend Next.js 16 build passing cleanly (`npm run build`). ESLint passing with zero errors on new components (`npm run lint`).
- **Database Architecture (`console_db`)**:
  - `app.pm_investigations`: Durable evidence-based bug investigations dispatched to Hermes (`project_id`, `task_id`, `run_id`, `workspace_id`, `external_job_id`, `title`, `status`: `queued`, `running`, `reproduced`, `suspected`, `inconclusive`, `failed`, `cancelled`, `symptom`, `reproduction_command`, `observed_result`, `expected_result`, `confidence`: `low`, `medium`, `high`, `evidence_locations`: JSONB list of file citations & line ranges, `suspected_causes`: text array, `proposed_fix`, `patch_artifact_id`: FK to `app.pm_artifacts(id)`, `missing_info`: text array, `telemetry`: JSONB, timestamps).
  - Seeded global agent definition: `slug: 'investigator'`, `role_type: 'worker'`, `name: 'Hermes Bug Investigator'`, and version 1 with immutable system prompt and tool constraints.
- **Backend Architecture (`app/modules/pm_hermes`)**:
  - `adapter.py`:
    - `BaseHermesAdapter`: Abstract contract specifying `capabilities()`, `submit()`, `get_job_result()`, and `cancel()`.
    - `LiveHermesCliAdapter`: Production adapter integrating local Nous Research Hermes Agent CLI (`/opt/homebrew/bin/hermes` v0.21.0). Creates strictly isolated project runtime homes (`HERMES_HOME=/tmp/csa-hermes-homes/project-<id>`) with clean `SOUL.md` and sandboxed directories (`sessions/`, `memories/`, `logs/`). Sanitizes runtime environment by completely removing app DB URLs, encryption keys, and external API tokens.
    - `StubHermesAdapter`: Deterministic offline adapter providing instant reproduction fixtures, canary memory isolation verification, and in-flight cancellation for fast, token-free CI/CD execution.
    - Factory function `get_hermes_adapter()` auto-detecting CLI vs stub based on `settings.pm_hermes_provider`.
  - `investigation_service.py`: Business logic managing workspace resolution, job dispatch, structured findings extraction, status updates, process cancellation, patch artifact generation (type `patch` in `app.pm_artifacts`), and audit logging (`pm.investigation.create`, `pm.investigation.cancelled`, `pm.investigation.patch_drafted`).
  - `router.py`: REST API endpoints mounted under `/api/admin/pm` (`GET /hermes/capabilities`, `GET/POST /projects/{pid}/investigations`, `GET /projects/{pid}/investigations/{iid}`, `POST /projects/{pid}/investigations/{iid}/cancel`, `POST /projects/{pid}/investigations/{iid}/patch`). Guarded by `require_admin` and `require_pm_hermes_enabled`.
  - `config.py` & `deps.py`: Added `pm_hermes_enabled: bool = True`, `pm_hermes_binary: str`, `pm_hermes_homes_dir: str`, `pm_hermes_timeout_seconds: int`, `pm_hermes_provider: str = 'auto'`, and fail-closed guard `require_pm_hermes_enabled`.
- **Frontend Architecture (`web/components/pm/hermes`)**:
  - `types.ts`: Added `EvidenceLocation`, `BugInvestigation`, and `HermesCapabilities` interfaces.
  - `InvestigateModal.tsx`: Modal allowing developers to launch autonomous bug reproduction jobs with titles, symptoms, stack traces, and suspected causes.
  - `InvestigationReportCard.tsx`: Rich evidence dashboard featuring status pills, confidence badges, 1-click reproduction command copying, expandable line-level file citations (`L45-52`), code snippet inspection, suspected causes, proposed fixes with a 1-click "Draft Patch Artifact" workflow, and runtime telemetry counters.
  - `TaskDrawer.tsx`: Integrated Hermes Bug Investigations timeline into the task detail drawer with live React Query synchronization and 1-click investigation launcher.
- **Verification & Test Evidence**:
  - Automated tests: 136/136 passing (`uv run pytest` in 3.34s), including 7 comprehensive tests in `tests/test_pm_hermes.py` covering fail-closed flags, capabilities discovery, canary memory isolation across projects (asserting distinct `HERMES_HOME` paths and env sanitization), fixture bug reproduction with line citations, investigation retrieval, job cancellation, and patch artifact generation.
  - Code Quality: Ruff check passing on all `app/modules/pm_hermes` and `tests/test_pm_hermes.py`.
  - Frontend Quality: ESLint passing (`npm run lint`), Next.js 16 production build compiling cleanly (`npm run build`).

### 7.9 Phase 08: Multi-Agent Workroom (Bounded Multi-Agent Collaboration)
- **Status**: Complete (`implemented_local`). Schema migration `0010_pm_workroom.sql` applied to local `console_db`. 9 comprehensive automated tests passing in `tests/test_pm_workroom.py` (145/145 total backend test suite). Frontend Next.js 16 production build passing cleanly (`npm run build`). ESLint passing with zero errors and warnings on all workroom components (`npm run lint`).
- **Database Architecture (`console_db`)**:
  - `app.pm_workroom_runs`: Multi-agent run execution state machine (`workflow_type`, `status`, `current_stage`, `active_role`, `repair_cycles`, `budgets`, `usage`, `review_bundle`). Separates intermediate execution stages (`patch_proposed`, `tests_passed_sandbox`) from human-controlled gates (`published_repository`, `deployed`).
  - `app.pm_workroom_messages`: Strongly-typed collaboration stream (`message_type`: `proposal`, `question`, `evidence`, `handoff`, `review`, `final`) enforcing strict project-level isolation.
  - Agent definitions & immutable version pinning: Global catalog seeded for 6 specialized roles (`planner`, `architect`, `investigator`, `implementer`, `reviewer`, `qa`). Project-level version pinning via `app.pm_agent_assignments` guarantees that upstream global prompt updates cannot alter existing project runs.
- **Backend Architecture (`app/modules/pm_workroom`)**:
  - `scheduler.py`: `WorkroomScheduler` tracking aggregate tokens, wall-time seconds, cost in USD, and active run concurrency. Enforces early halt (`budget_exceeded`) upon exceeding thresholds ($1.00 max cost, 100k tokens, 900s wall-time, 2 max repair cycles).
  - `coordinator.py`: `WorkroomCoordinator` managing the deterministic bug-fixture state machine:
    1. Investigator reproduces symptom with file & line citations (`evidence`).
    2. Implementer drafts patch (`handoff`).
    3. Reviewer rejects intentionally incomplete fix with line citations (`review`), initiating repair cycle 1.
    4. Implementer repairs patch (`handoff`).
    5. Reviewer approves (`review`).
    6. QA verifies patch in sandbox (`evidence`), advancing status to `tests_passed_sandbox`.
    7. Synthesizes operator review bundle (`review_ready`).
  - `conflict_service.py`: `ConflictService` analyzing diff collisions across concurrent branches/tasks and generating reviewable conflict patch artifacts in `app.pm_artifacts`.
  - `workroom_service.py`: Roster management, agent version pinning with policy overrides, isolated collaboration queries, workflow execution dispatch, and human operator intervention handlers (`pause`, `resume`, `guidance`, `revision`, `cancel`).
  - `router.py`: REST endpoints mounted under `/api/admin/pm/projects/{pid}/workroom` (`/roster`, `/pin`, `/runs`, `/runs/{rid}`, `/messages`, `/intervene`, `/conflict-check`, `/budget`).
  - `deps.py` & `config.py`: Added `pm_workroom_enabled: bool = False` (activated in dev `.env`), scheduler limits, and fail-closed dependency guard `require_pm_workroom_enabled`.
- **Frontend Architecture (`web/components/pm/workroom`)**:
  - `WorkroomRoster.tsx`: Displays cards for the 6 discrete agent roles with capability badges, current pinned version, and pinning modal with JSON policy overrides.
  - `HandoffTimeline.tsx`: Visual multi-agent workflow progression bar across Investigator, Implementer, Reviewer, and QA with stage status badges.
  - `CollaborationFeed.tsx`: Real-time typed message stream with message type filters (`proposal`, `question`, `evidence`, `handoff`, `review`, `final`), line-level code citations, and human operator chat prompt injection.
  - `BudgetPanel.tsx`: Live telemetry meters for Wall Time (sec), Token Usage, Estimated Cost (USD), and Repair Cycles with warning thresholds.
  - `InterventionBar.tsx`: Operator controls for Pause, Resume, Inject Guidance, Request Revision, and Cancel Run.
  - `ConflictReviewModal.tsx`: Visual modal simulating concurrent branch diff integration and conflict marker review.
  - `WorkroomWorkspace.tsx`: Master workroom panel integrating all sub-components.
  - `app/(admin)/projects/[id]/page.tsx`: Activated `workroom` tab ("AI Workroom"), mounted `WorkroomWorkspace`, and updated roadmap checklist.
  - `components/ui/kit.tsx`: Added accessible `Modal` component with backdrop click-to-close and Escape key support.
- **Verification & Test Evidence**:
  - Automated tests: 145/145 passing (`uv run pytest` in 42.09s), including 9 comprehensive tests in `tests/test_pm_workroom.py` covering fail-closed flags, roster & pinned version immutability, cross-project message isolation, typed messages, bug-fixture workflow execution, budget exhaustion early halt, concurrent conflict detection, operator intervention, and budget telemetry.
  - Code Quality: Ruff check passing on `app/modules/pm_workroom` and `tests/test_pm_workroom.py`.
  - Frontend Quality: ESLint passing with zero errors (`npm run lint`), Next.js 16 production build compiling cleanly (`npm run build`).
