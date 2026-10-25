# Dedicated staging ingress: activation, acceptance and rollback

Status: public-demo exposure revision, **not executed**. This consolidates the next
owner-run gates so they can be reviewed in one PR. It does not authorize any
operation. Start with [release authority and evidence](RELEASE.md), the
[local Compose proposal](compose.staging-local-proposal.yml), and the current
[continuation](../docs/handoffs/2026-10-19-domain-release-next-session.md).

**Current hosting choice:** the owner subsequently selected the existing host
tunnel plus Caddy. Follow [STAGING_SHARED_CADDY.md](STAGING_SHARED_CADDY.md)
for that topology, startup overlay, Caddy/route changes and rollback.
The dedicated-tunnel/token/connector instructions below are an alternative,
not steps to execute for shared hosting. Public-login safeguards and
application acceptance requirements below still apply. Shared hosting does
not itself solve abuse controls or public proxy acceptance.

## Scope and starting state

PR #21 merged as `05b429ec0856694de87dfb02bbd7ca74cbbf27bd`; main CI
`39951244406` passed. Last owner-reported state: database, gateway and frontend
stopped; both staging volumes retained after successful private readiness.
Accepted Python/frontend source is `7cdc156`, not the latest procedure commit.
Do not rebuild/pull images, repeat bootstrap, regenerate application secrets
or delete volumes to continue.

Only `staging-console.fourstacks.my` is proposed. The dedicated connector
reaches `http://frontend:3000` over the internal `edge` network, with its own
project-scoped outbound network. Gateway and database are not tunnel origins.
No Caddy, host port, shared network, shared connector, apex/www, wildcard
hostname, private-network/CIDR route or existing website change is included.
HTTP inside the isolated edge network is intentional; public HTTPS terminates
at Cloudflare. Do not turn off public TLS verification to pass acceptance.

**Selected audience:** the owner selected a publicly reachable login page,
without Cloudflare Access, and owner-only CSA login for a presented demo.
Anonymous visitors must not gain console access. There is no visitor account,
shared administrator password, public signup or anonymous tenant enrollment.
Screen-share only synthetic content and avoid displaying identities, keys,
browser storage or session tokens. This replaces the earlier owner-only Access
recommendation; it does not approve publication or remove authentication gates.

This staging scope is console authentication/readiness and explicitly selected
synthetic tenant operations. Worker, delivery and KB consumers are absent.
Do not submit model turns, provider operations or ingestion work, or claim
end-to-end agent conversations. CD automation follows acceptance of this
process, not merely tunnel connectivity.

## Official guidance checked on 2026-10-23

- [Dashboard tunnel creation and published applications](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/):
  remotely managed tunnel; active Cloudflare domain before publication; a
  published application maps a hostname to a service URL.
- [Run parameters](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/):
  token-file authentication is for remotely managed tunnels. The pinned
  connector's offline token-file/UID evidence is recorded in RELEASE.
- [Access self-hosted public application](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/):
  create Access protection before the tunnel route to avoid an unprotected
  interval; policies deny by default; cloudflared can validate Access tokens
  through **Protect with Access**.
- [Connectivity prechecks](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/troubleshoot-tunnels/connectivity-prechecks/):
  outbound tunnel traffic uses port 7844 (UDP/QUIC or TCP/HTTP2). DNS and
  management connectivity also matter. Network probes/firewall changes need
  separate approval; do not widen the host firewall speculatively.

The documentation connector failed authentication; the references above were
read directly from official pages instead. Two older documentation URLs returned
404 before the current paths were located. Recheck guidance before execution
if it has changed. These documentation reads did not inspect account or DNS
state; subsequent owner-reported dashboard findings are recorded below.

## Owner-reported discovery and shared-route cleanup

The owner reported the zone Active, an existing wildcard DNS record, and
existing apex/www routes serving a separate company landing page. No dedicated
staging tunnel exists. The owner initially confused a tunnel route with an
Access application; the later explicit Access Applications check found the
list empty. Do not treat the earlier coverage answer as evidence of protection.

Before dedicated setup, the owner added the staging hostname to the shared
tunnel with `http://frontend:3000` and received Cloudflare 502. At last report
the staging services were stopped; the shared connector was not configured
for staging's isolated edge network. No runtime diagnostic established the
exact upstream failure. Waiting was not an acceptance strategy.

After separately approving removal of only the new staging route, the owner
reported it deleted, both company landing-page routes still present, and no
explicit staging DNS record in the displayed list. The wildcard remains:
staging may still resolve to an existing unrelated origin. Do not interpret
its browser response as CSA ingress, no-exposure proof or DNS nonexistence.
No Caddy change, shared connector restart or DNS deletion was requested.
These are owner reports, not independent assistant account/DNS verification.
Do not copy DNS values, account links or personal identifiers into this record.

## Gates and decisions

Record non-secret decisions before issuing execution commands:

| Gate | Required decision/evidence | Operation needs explicit approval |
| --- | --- | --- |
| Authority | Owner controls intended account/active zone; recheck exact hostname conflicts and wildcard coverage; preserve existing wildcard and company routes | Read-only dashboard inspection; no credential/API-token sharing |
| Exposure | Public login page, owner-only CSA account, no Access; review safeguards below and agree demo window/withdrawal | Public exposure, edge rules or future Access changes |
| Tunnel | Dedicated staging-only name and identity; no existing/shared tunnel reuse | Tunnel creation |
| Secret | Private token entry on host, owner/mode checks, no overwrite | Token provisioning |
| Runtime | Accepted procedure merge/CI; accepted image digests remain locally available; both volumes remain | Database/app restart and private checks |
| Connector | Frontend healthy, exact service URL, Access enforcement configured if selected | Dedicated connector start and external connectivity |
| Route | Safeguard gates satisfied; exact-host record supersedes wildcard only for staging; preserve wildcard/apex/www | Published route/DNS creation |
| Acceptance | Controlled accounts/tenants and expected denials agreed | Browser, synthetic mutations, failure/recovery checks |
| Closeout | Results and limits recorded; protected identities/secrets retained safely | Stop, route removal, token revocation or publication as applicable |

Cloudflare Access is optional for Tunnel. For this selected demo, do not create
an Access application or turn on **Protect with Access** as an unannounced
requirement. Anyone can reach the public login page; CSA authentication and
authorization remain mandatory. If a covering Access app is later discovered,
stop rather than remove it or add a Bypass rule. A future audience change needs
its own review. No IdP, email service or paid plan is implied.

### Public-login safeguards and publication hold points

Source review compares the accepted `7cdc156` image revision with this slice's
base `05b429ec`; identity/auth/security and identity SQL are unchanged.
The following source behaviour is not yet public browser acceptance:

| Surface | Implemented behaviour | Remaining acceptance / limitation |
| --- | --- | --- |
| Password work | Salted scrypt, 12-1024 character new passwords, at most two concurrent derivations per process | Not a global request/memory/queue bound or DDoS guarantee |
| Login attempts | Database buckets: default 5 attempts/email and 50/IP per 900-second window; 429 with Retry-After above limits; successful login clears email bucket | Verify actual proxy client-IP grouping. Counts include attempts, not only failures; do not throttle the real admin to test |
| Errors/reset | Generic failed-login response; public reset returns a generic manual-support message, not a reset credential | Verify no identity enumeration through public responses; no automated email delivery is implied |
| Request authority | Login/mutations require configured Origin and CSRF; protected routes derive identity from session, not browser role headers | Correct CSRF can be obtained by any visitor; CSRF is not bot protection |
| Session | Secure, HttpOnly, SameSite=Lax session cookie; separate readable CSRF cookie; opaque hashed persisted tokens; idle/absolute expiry | Browser/proxy cookie handling, logout and cache isolation remain to test |
| First login | Password-change-required gate; password change revokes existing sessions and issues replacement session | Owner completes change privately; no shared admin credentials |

Before approving public routing:

1. Confirm no real customer data/provider credentials are in the demo scope.
   Verify exact images, networks, secure-origin settings and private readiness
   using the accepted procedure. No public gateway/DB ports or legacy catch-all.
2. Agree a short owner-attended demo window and explicit withdrawal sequence.
   Visitors receive no credentials; do not advertise an operational AI service.
3. Review available exact-host Cloudflare abuse/rate controls in the owner's
   plan before selecting changes. Do not assume a paid feature exists, enable
   account-wide rules or spend money. Public auth endpoints require more than
   a login-page GET rule; do not place interactive challenges on JSON requests
   without checking browser compatibility.
4. Application throttling is implemented, but its per-IP behaviour behind the
   tunnel/Next.js proxy and aggregate request protection remain unverified.
   Record the chosen edge control, measurable limits and compatibility test
   before publication. If an adequate control cannot be configured under the
   approved plan, hold routing for review; do not claim app throttling alone
   proves resistance to Internet abuse or silently disable it.
5. After individually approved routing, immediately run anonymous-denial and
   owner-login checks below. Withdraw the staging route on failed authority,
   cookie, CSRF or unexpected exposure results. Infrastructure creation may be
   prepared separately, but public routing stays blocked until these
   preconditions and the explicit exposure approval are satisfied.

For conflicts, stop: do not overwrite DNS or modify a wildcard/shared policy.
Record only sanitized resource names/IDs and decisions; no token-bearing
installation command, screenshots, cookies or full dashboard exports in chat.

## Owner-run sequence (each gate separately approved)

### 1. Create the dedicated tunnel, without installing another service

In the intended Cloudflare account, open **Networking > Tunnels**, create a
dedicated remotely managed tunnel (suggested name `csa-staging-console`), and
record its non-secret ID locally. Do not add a published route yet.

Do not run the dashboard's generated `docker run`, host installer or service
installation command: these can contain a token, use a mutable image, or create
a second connector outside Compose. Obtain only the dedicated tunnel token
through the owner's private dashboard session. Do not paste it into chat,
argv, shell history, a repository file or an environment variable.

### 2. Provision only the connector secret

The application secret checker covers ten application files, not this token.
The connector file must be owned by `65532:65532`, mode `0400`, under a restricted
host directory. Compose file-backed secrets do not remap host ownership.
The following privileged operation is **not authorized by this runbook**.
After separate approval, run it interactively on the staging host:

```bash
sudo python3 - <<'PY'
import getpass
import os
import stat
import warnings
from pathlib import Path

warnings.simplefilter("error", getpass.GetPassWarning)
if os.geteuid() != 0:
    raise RuntimeError("Root is required only for secret ownership provisioning")
base = Path("/srv/csa-staging/secrets")
for directory in (Path("/srv"), base.parent, base):
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        raise RuntimeError("Expected a real, non-group/world-writable staging parent")
target_dir = base / "connector"
target_dir.mkdir(mode=0o700)
target_dir.chmod(0o700)
target = target_dir / "staging_tunnel_token"
token = getpass.getpass("Dedicated staging tunnel token (hidden): ")
if not token or any(character.isspace() for character in token):
    raise ValueError("Expected a non-empty single-line tunnel token")
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
with os.fdopen(fd, "w", encoding="utf-8") as output:
    os.fchown(output.fileno(), 65532, 65532)
    os.fchmod(output.fileno(), 0o400)
    output.write(token)
    output.flush()
    os.fsync(output.fileno())
del token
print("Connector secret created; values not displayed")
PY
```

This reads only the newly supplied token; it does not read application secrets.
It refuses an existing connector directory/file. Failure may leave an empty
directory or partial file: stop for bounded inspection and recovery rather
than rerunning, overwriting or broadening permissions.

Approved metadata-only verification:

```bash
sudo stat -c '%u:%g %a %F' \
  /srv/csa-staging/secrets/connector \
  /srv/csa-staging/secrets/connector/staging_tunnel_token
```

Expect a root-owned directory `0:0 700 directory` and file
`65532:65532 400 regular file` (wording may vary by locale). Never use `cat`,
`docker inspect` environment dumps or token-bearing logs. The token is
unencrypted at rest; host backup exposure remains to assess.

### 3. Restart and privately verify the accepted applications

Verify the accepted procedure commit and full hosted CI first. Prepare a clean
detached checkout of that merge without modifying another worktree. After
separate restart/probe approval, use the complete
[private startup procedure](RELEASE.md#private-staging-application-startup-procedure-only-not-run),
substituting the newly verified clean checkout path. Do not use `stg`, which
conflicts with an installed executable. Require all three services healthy
and all documented private checks passing. Do not repeat migrations/bootstrap.

### 4. Start only the dedicated connector

After separate approval, the following block uses the already accepted Compose
configuration. Confirm the directory still contains accepted merge `8ff63c29`
and no changes before issuing it. It starts no dependency and forbids pulls.
Do not interpret `running` as tunnel health: no untested healthcheck is added.

```bash
(
  cd /home/amsyrzmi/CSAV2-staging-private-run || exit 1
  test "$(git rev-parse HEAD)" = 8ff63c29a37b8b9802b668e14594da286f7c6f12 || exit 1
  test -z "$(git status --porcelain)" || exit 1
  export NO_LOCAL_ENV=1 ENV=ci COMPOSE_PROFILES=
  export CSA_STAGING_PROJECT=csa-staging
  export CSA_STAGING_SECRET_DIR=/srv/csa-staging/secrets
  export CSA_STAGING_PYTHON_IMAGE=ghcr.io/amsyrzmi/csa-python@sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764
  export CSA_STAGING_FRONTEND_IMAGE=ghcr.io/amsyrzmi/csa-frontend@sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f
  export CSA_STAGING_CONSOLE_ORIGIN=https://staging-console.fourstacks.my
  docker compose --env-file /dev/null --project-name csa-staging \
    --file deploy/compose.staging-local-proposal.yml --profile staging-approved \
    up --detach --no-deps --pull never cloudflared || exit 1
  docker container inspect csa-staging-cloudflared-1 \
    --format 'status={{.State.Status}} user={{.Config.User}} read-only={{.HostConfig.ReadonlyRootfs}} ports={{json .HostConfig.PortBindings}} networks={{json .NetworkSettings.Networks}}'
)
```

Expected: running, user `65532:65532`, read-only, no published ports, and only
`csa-staging_edge` plus `csa-staging_connector-egress`. Check the dedicated
tunnel's dashboard reports Healthy before routing. Dashboard connector health
does not prove frontend reachability. If it fails, stop; do not pull a different
image, run as root, relax token permissions or connect to a shared network.

### 5. Route only the staging hostname after safeguard approval

With the safeguard hold points satisfied and separate public-routing approval,
configure the dedicated tunnel's published
application route with hostname `staging-console.fourstacks.my`, no path filter,
service **HTTP**, URL `frontend:3000` (effective URL `http://frontend:3000`).
For the selected public-login model, do not enable **Protect with Access** or
create an Access app. CSA login stays required. Do not configure a second route to the gateway, TCP database,
host localhost, or an existing Caddy listener. Do not add wildcard/CIDR routes.

Adding the route can create DNS automatically for a full Cloudflare zone.
Treat that as a DNS write needing approval; do not also create a duplicate
CNAME manually. With partial/external DNS, stop and review the provider-specific
operation. Verify the exact record targets only the dedicated tunnel and the
exact hostname has valid public HTTPS. Do not change apex/www records,
nameservers or existing certificates.

## Acceptance matrix (no checks executed by this document)

Use owner-controlled browser profiles, unique staging passwords and only
synthetic tenant fixtures. Never export HAR files, cookies, CSRF tokens,
passwords or response bodies containing identities. Record status/results
and sanitized counts. The selected model has no Access perimeter; an
anonymous browser must reach the login page but not protected CSA resources.

| Check | Acceptance / failure condition |
| --- | --- |
| Public TLS and boundary | Exact staging hostname has trusted HTTPS; no public gateway/database port. Stop for TLS errors or unexpected host routing; never bypass warnings |
| Public-login boundary | Anonymous `/login` loads without an Access challenge; `/console/v1/auth/me` denies anonymous requests and protected pages redirect/deny; no protected data in HTML or cached responses |
| Abuse controls | Approved exact-host limits work without breaking CSRF/login/password change; controlled synthetic failures produce generic errors and the expected 429/Retry-After, without locking out the real admin or load-testing the home server |
| CSA login | Anonymous protected page redirects to login or gets contract-standard denial; valid staging admin reaches forced password change, not unrestricted console |
| Initial password change | New unique password accepted; old password/session cannot grant continued access as specified by current identity contracts. Relogin works |
| Cookies and CSRF | Session Secure/HttpOnly/SameSite attributes match source; legitimate same-origin mutations work; missing/wrong CSRF or disallowed Origin is denied without mutation |
| Logout/revocation | Logged-out or revoked session cannot access protected reads/writes; browser back/cache does not reveal protected responses |
| Tenant authority | Separately approve two synthetic tenants and scoped admin/staff/reader identities. No membership means denial, including a platform admin where membership is required |
| Cross-tenant requests | Controlled tenant-A identity cannot read/mutate tenant-B resources by changing IDs or tenant selection; compare before/after state, not status alone |
| Role denials | Reader cannot mutate; staff cannot perform admin actions. Use harmless synthetic fixtures, not provider writes or invented APIs |
| Legacy boundary | Requests through frontend to `/api/v1/channels/tiktok_shop/oauth/authorize`, `/oauth/callback` and webhook paths return frontend denial, not gateway legacy output; use exact mounted paths from current source |
| Cache safety | Protected identity/tenant responses are not shared/cached between controlled browser sessions |
| No unsupported consumers | Do not enqueue real turns, delivery or KB jobs; UI must not be presented as fully operational agent service |

Read the current [console contract](../docs/CONSOLE_API.md), identity source
and tests before selecting browser actions or HTTP methods. Do not handcraft
credential-bearing curl commands. If a required UI/API operation is missing,
mark it blocked rather than substituting a fake passing check. Provisioning
the second tenant/identities is a separate write approval, not a bootstrap rerun.

## Failure drills and rollback

Agree the desired end state and approve each drill first. Preserve both volumes
and the administrator identity. There is no `down -v`, database reset, secret
regeneration or shared-service restart in this plan.

1. **Connector loss:** stop only `csa-staging-cloudflared-1`. The dedicated
   tunnel loses its connector; no staging origin should remain reachable through
   an unintended replica/route. Assess Cloudflare error/Access behaviour without
   assuming a particular error code. Restart only the dedicated connector using
   the approved block and repeat HTTPS/login checks. Unexpected replicas fail
   isolation acceptance; do not stop an unknown/shared connector.
2. **Gateway loss:** stop only gateway. Frontend-dependent requests must fail
   safely, not return stale authenticated success; a static login page alone
   may remain 200. Restart gateway explicitly with `--no-deps --pull never`
   and a bounded health wait; rerun private readiness and public identity checks.
3. **Database loss:** stop only staging database after explicit drill approval.
   Gateway `/readyz` must become non-200, then recover after database restart
   and successful private checks. Do not alter migration metadata on persistent
   staging to simulate failure. Existing gateway behaviour may require bounded
   reconnect time; record actual timings and failures.
4. **Exposure rollback:** disable/remove only the newly created staging
   published route and its dedicated DNS record after verifying resource
   ownership. No Access policy is created by this plan; preserve any other
   protections that exist. Stop the
   dedicated connector; then frontend/gateway, then database. Confirm all
   stopped, both volumes remain, and the staging hostname no longer exposes
   the    CSA origin. Removing the explicit DNS record restores wildcard coverage;
   it does not make the hostname nonexistent or necessarily inaccessible.
   Verify withdrawal of the CSA mapping in the dashboard and through an
   approved request; do not edit or test the wildcard's unrelated origin.
   DNS/cache behaviour can affect observed results.
5. **Token incident:** stop dedicated connector, rotate/revoke only its tunnel
   credential through an approved owner procedure, then explicitly provision
   the replacement. No token or previous file content goes into logs/chat.
   Do not delete a tunnel or token that belongs to another service.

Exact owner-run stop primitives (each separately approved; choose only the
containers intended for that drill):

```bash
docker container stop csa-staging-cloudflared-1
```

```bash
docker container stop csa-staging-gateway-1
```

```bash
docker container stop csa-staging-staging-db-1
```

For full shutdown after ingress is disabled, separately approve connector stop
then use the [non-destructive application stop](RELEASE.md#private-staging-application-startup-procedure-only-not-run).
If a resource is absent, stop and adjust the exact command rather than hiding
failure or targeting all containers.

For gateway/database drill recovery, the complete private startup block in
RELEASE provides the exact ordered `up --no-deps --pull never` commands and
private checks; obtain approval to rerun that block against the accepted
checkout. It restarts existing services without invoking migration/bootstrap.
For connector recovery, use the complete connector-only block above after
separate approval. Do not run a profile-wide `up` as a shortcut.

**Backup/restore gate remains open.** The volumes now contain protected staging
identity data. Define encrypted backup storage, application-key retention,
access/retention, a separate restore target, credential reprovisioning and a
restore verification matrix before exporting data or running a restore.
The earlier disposable synthetic restore drill is not acceptance for these
persistent volumes. No backup destination, real dump or destructive restore is
authorized here.

## Consolidated closeout and protected CD prerequisites

Collect results for all approved gates in one evidence update, rather than a
PR per command. Preserve failures, timings, skips, blocked checks and the final
running/stopped/route state; do not record secrets or personal identifiers.
One current continuation must identify the next unmet gate.

Only automate a demonstrated process. A later protected CD slice needs:

- main/reviewed source and successful full CI; exact artifact revision,
  manifest/checksum identity and runtime-user checks for each new build;
- protected environment approval, narrowly scoped registry/host authority,
  staging-only secret references and no credentials in artifacts/logs;
- serialized promotions, explicit project/hostname allowlists and finite
  readiness/acceptance waits; no mutable image tags or implicit provider enablement;
- separately controlled migrations, volume/backup compatibility and no automatic
  destructive downgrade; a previous image is not necessarily schema-compatible;
- failure handling that records truthfully failed promotion, preserves data,
  restores the approved route/service state and demonstrates recovery;
- authenticated browser/tenant/ingress acceptance and a tested restore plan.

Do not claim protected staging CD or production deployment from this runbook,
a green render, a healthy connector or a reachable login page.
