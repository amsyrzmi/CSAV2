# Permission, execution and verification requirements

These controls are necessary for a system that executes project code and stores tenant data. Implement them in the owning phases; phase 10 performs fault/load verification.

## Permission matrix

| Operation | Platform admin | Client owner, phase 09 | Client member, phase 09 | Agent/service |
|---|---|---|---|---|
| Create internal project | Yes | No | No | No |
| Create client project | Authorized client selection | Own tenant under existing policy | Existing explicit policy only | Proposal only |
| Edit PM tasks/milestones | Yes | Narrow configured project permission | Comment/request by default | Exact approved run capability |
| Read notes/artifacts | Authorized project | Shared revisions in permitted projects | Shared revisions in permitted projects | Scoped context only |
| Publish a client-facing revision | Yes with review | Explicit owner policy if enabled | No by default | Draft only |
| Edit global agent definitions | Yes | No | No | No |
| Create/attach terminal | Admin and session owner policy | No | No | No browser session; scoped runner job |
| Execute project tests/code | Approved project runner policy | Request only | Request only | Isolated sandbox and budget |
| Publish repository changes | Separate reviewed action | Request only | No | Scoped approval and credential |
| Deploy/change AWS/live DB | Separate operational policy | No | No | Disabled in PM MVP |

Existing behavior may differ for legacy project creation. Preserve it intentionally and record it; do not quietly broaden permissions while adding PM.

## Boundaries that need enforcement

- Resolve ownership for every child object, download, cursor, search result, event and checkpoint. A globally unique ID is not permission.
- Definition/version/memory text never authorizes tools. Credentials are references resolved server-side, excluded from prompts and client DTOs.
- Treat repo code, README/AGENTS.md, logs and notes as untrusted inputs. Prompt injection tests must attempt cross-tenant reads, secret disclosure and policy overrides.
- Effective permissions come from trusted server context. An agent cannot select an arbitrary tenant, path, shell backend, model credential or checkpoint thread.
- Shell sandbox: separate execution identity, controlled mounts/egress/resources, no production secrets or Docker socket. A cwd/path-prefix check alone is insufficient; filesystem boundaries must resist symlinks and mounts.
- HTTP CSRF handling does not secure WebSocket upgrades. Validate WS Origin/auth and ongoing revocation explicitly. Revalidate SSE sessions; filter replay as strictly as the initial response.
- Checkpoints and leases are necessary but insufficient for exactly-once side effects. Action IDs, fencing, provider receipts and reconciliation protect replay/crash windows.
- Approval covers a canonical action hash, base commit, policy and budget, has an expiry, and is atomically consumed. Modified action content or base state invalidates it.
- Renderers do not fetch arbitrary URLs/read host files or accept user configuration overrides. SVG/Markdown exports must be safe to view.
- Raw terminal output may contain secrets. Limit access and retention rather than claiming perfect automatic masking. Sanitized summaries are separate artifacts.

## Critical tests by phase

| Phase | Required evidence |
|---|---|
| 00 | Current baseline with no live domain write tests |
| 01 | Legacy project migration fixture; internal/client isolation; old endpoint compatibility |
| 02 | Task version conflicts, ordering, project constraints, dependency cycles under concurrency |
| 03 | Revision/backlink integrity; scoped search; safe Markdown and traversal rejection |
| 04 | Durable restart/resume; duplicate acceptance; scoped SSE replay; budget/cancel; injection attempts |
| 05 | Supported fixture renders; invalid/oversized input; no remote includes; safe export |
| 06 | Actual runner isolation/egress; WS forgery and revocation; process termination |
| 07 | Hermes capability contract; distinct runtime homes; bug fixture evidence; crash reconciliation |
| 08 | Bounded handoffs; aggregate budgets; patch conflicts; independent review |
| 09 | Client DTO/content review; shared revision lifecycle; owner/member checks and export isolation |
| 10 | Backup restore, release rollback, lease takeover, unavailable services and capacity checks |

Use deterministic stub models for CI and representative PostgreSQL fixtures for transactions/migrations. Label real-model smoke tests separately. Do not make CI dependent on the personal Hermes agent or production client data.

## Quality and rollout checks

Follow actual scripts discovered in phase 00. The supplied state suggests `uv run pytest`, `npm run lint`, `npm run build` and repository lint commands; verify working directories and installed tools first. Run targeted tests then applicable existing regressions. Verify production build for substantial new client components, and staging ingress tests for SSE/WS.

Record counts/results from actual commands. This pack has not executed your app's test suite, rendered diagrams, contacted your servers, or validated Hermes runtime behavior. Its accompanying diagnostic scripts are syntax-checked and designed to read metadata, not proof of infrastructure readiness.
