# Phase 06 — Dedicated project workspaces and terminal

Prerequisite: phase 01, plus an actual isolated runner provisioned by the operator. Hosted project terminal first; a laptop companion is a separate extension. A shell with a different cwd alone is not project isolation.

## IDE AI work

1. Implement repository registration and private workspace provisioning with server-owned locators. Validate remote URLs/protocols, handle credentials via short-lived/ref-scoped secrets, and prevent SSRF/path traversal. Clone approved sources without automatically running hooks, scripts or submodules.
2. Add runner capability discovery and unavailable/degraded handling. Workspace provisioning requests select a registered project/repo/base commit, not an arbitrary host path or Docker options.
3. Add PTY service and xterm.js UI with resize, tab lifecycle, detach/reattach, capped scrollback, owner labeling and explicit runner location. Authenticate WebSocket upgrade and exact allowed Origin separately from HTTP CSRF. Use the production ingress for WS upgrades; do not assume Next HTTP rewrites proxy WS correctly.
4. Create terminal-session resources through a CSRF-protected POST. WS attach to an existing session is read/write attachment only, not an unauthenticated shell-creation endpoint. No durable bearer tokens in query strings/localStorage. If a short-lived attach ticket is necessary, make it single-use and scoped to session/user/workspace/origin.
5. Enforce admin/owner permission, session revocation/expiry and idle timeout on the ongoing socket. Define text/binary frame size, rate limits, resize validation, reconnection behavior and termination acknowledgments.
6. Run shells in the isolated runner under non-root identities and mount only the approved workspace. No Docker socket, app DB credentials, SSH agent forwarding, host home, or production secrets. Restrict network routes to internal/production services; allow only approved dependency/model destinations through a controlled egress path. Fixed runner templates are server-controlled.
7. Audit create/attach/terminate and agent commands. Treat terminal output as potentially sensitive; do not promise perfect redaction of an arbitrary interactive shell. Restrict transcript retention and keep raw output out of client activity/audits.
8. Human terminal and agent coding runs use separate worktrees. Changing the browser's current project cannot rebind an existing PTY. Archive/revoke denies new sessions and terminates or reconciles existing ones.

## Acceptance

Open terminals for project A and B and prove they cannot read each other's files or connect to console/domain DB services. Attempt forged Origin, another user's PTY, stale ticket, revoked cookie and path traversal; all fail. Reconnect without replaying commands; terminate and verify the actual process ends. Runner offline yields a useful disabled state. Test production ingress WS behavior with the actual route.

## Operator work

Provision a dedicated runner VM/host if possible. If containers share the homeserver kernel, document the lower isolation level and keep production mounts/privileged access away from the runner; evaluate untrusted customer code separately. Configure network controls, repository read credentials, persistent storage and limits. Never install a public unauthenticated browser shell on the app host. Local laptop mode needs pairing and explicit workspace registration later.
