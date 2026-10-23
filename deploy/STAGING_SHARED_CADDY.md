# Shared host tunnel and Caddy: CSA public-login demo

Status: configuration proposal, not applied. This is the owner's selected
alternative to the [dedicated connector plan](STAGING_INGRESS.md).
It supersedes that plan's dedicated-tunnel/token/network requirements for
this mode only, not application authentication, approval, acceptance or
recovery gates. No paid Cloudflare plan or Access application is required.
This document does not authorize infrastructure changes.

## Selected topology

```text
HTTPS staging-console.fourstacks.my
  -> existing shared Cloudflare Tunnel (host system service)
  -> HTTP 127.0.0.1:80, original Host preserved
  -> staging-only Caddy site
  -> HTTP 127.0.0.1:18030
  -> frontend:3000 -> private gateway:8080 -> private staging database
```

The owner explicitly chose shared hosting rather than a dedicated connector.
Only the frontend gains a host port, bound to loopback. Do not bind it to
`0.0.0.0`, reuse another app's port 3000, publish gateway/database, connect
shared services to data networks or enable the unused dedicated connector.
The configuration changes no existing landing-page route or Caddy block.
Check port 18030 again before startup; it was unused during preparation.

The overlay adds a normal, non-internal `frontend-host` bridge used only by
frontend, alongside its existing internal app/edge networks. This permits the
host-publishing topology and grants frontend outbound capability; it is not an
egress restriction. Gateway/database must never join this bridge. The published
port must remain loopback-only. The dedicated proposal is unchanged.

**Execution hold (2026-10-23):** the first approved shared-mode startup on
Docker 29.1.3 left all three services healthy but frontend unreachable from
the host (curl exit 7, HTTP 000). Docker recorded the requested loopback port
but actual port `3000/tcp` was null; both attached networks were internal.
The frontend-only normal bridge is a proposed correction, not yet recreated
or runtime-accepted. Do not proceed to Caddy/public routing based on a
passing render. Services were left running privately with owner approval.

Use the original [Compose proposal](compose.staging-local-proposal.yml)
**plus** [shared-Caddy overlay](compose.staging-shared-caddy.yml). Compose
2.24.4+ is required for `!override`: the connector's profile is replaced, not
union-merged, so `staging-approved` excludes it. The original proposal still
has its previous dedicated-connector behaviour if used without the overlay.
No startup occurs without explicit approval profiles. Use the overlay for
all subsequent shared-host operations; do not switch modes inadvertently.

The proposed [Caddy site](staging-console.Caddyfile) accepts only connections
from loopback, then proxies to the frontend. This avoids exposing the new
site directly on Caddy's existing public HTTP listener. It trusts the local
host tunnel process, not arbitrary remote connections or spoofed
`X-Forwarded-For`. Do not add global proxy trust or trust arbitrary forwarded
headers. Any future remote connector topology needs review of this restriction.
Caddy does not obtain a new origin certificate for this HTTP site; public
HTTPS is Cloudflare's responsibility.

**Audience:** anyone may see the login page, but only the owner signs in to
present synthetic content. No public signup, visitor accounts, shared admin
credentials, provider delivery, worker/delivery/KB consumers or end-to-end
agent-conversation claim. Shared tunnel and Caddy availability now affect CSA;
this is an intentional tradeoff. A bad Caddy reload can affect other sites,
so validate the entire candidate configuration and prepare rollback first.

## Gates before execution

- Full hosted CI and user merge of this exact configuration.
- Clean accepted checkout, previously accepted image digests locally present,
  existing database/upload volumes and unchanged staging secret files.
- No exact staging DNS/route conflict; preserve wildcard, apex/www and mail.
  Owner previously reported removing the accidental staging route after 502.
- Confirm the shared connector really runs on this host as reported and that
  it can use `http://127.0.0.1:80`. Do not reinstall, restart or inspect its
  secret-bearing command/environment.
- Public-login safeguards and unresolved proxy/aggregate-abuse checks from
  [the exposure review](STAGING_INGRESS.md#public-login-safeguards-and-publication-hold-points)
  still apply. Shared hosting is not a rate-limiting solution.
- Cloudflare Free was observed to lack Hostname in rate-limit expressions.
  No rule was deployed. A path-only rule could affect other hostnames:
  confirm path ownership and review measurable thresholds/compatibility before
  approving it. No zone-wide rule, purchase or protection guarantee is implied.
- Separate approvals for startup, host Caddy edit/reload, public route,
  authentication tests/mutations and stop/rollback. Local prep approval is not
  approval for any of these operations.

## 1. Render the configuration without starting

After verifying the accepted checkout, the following block uses no secret
values in command arguments and disables automatic environment-file loading.
It reuses current accepted image identities; newer builds need new acceptance.

```bash
(
  cd /home/amsyrzmi/CSAV2-staging-public-demo || exit 1
  export NO_LOCAL_ENV=1 ENV=ci COMPOSE_PROFILES=
  export CSA_STAGING_PROJECT=csa-staging
  export CSA_STAGING_SECRET_DIR=/srv/csa-staging/secrets
  export CSA_STAGING_PYTHON_IMAGE=ghcr.io/amsyrzmi/csa-python@sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764
  export CSA_STAGING_FRONTEND_IMAGE=ghcr.io/amsyrzmi/csa-frontend@sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f
  export CSA_STAGING_CONSOLE_ORIGIN=https://staging-console.fourstacks.my

  csa_shared_compose() {
    docker compose --env-file /dev/null --project-name csa-staging \
      --file deploy/compose.staging-local-proposal.yml \
      --file deploy/compose.staging-shared-caddy.yml \
      --profile staging-approved "$@"
  }
  csa_shared_compose config --quiet || exit 1
  csa_shared_compose config --services
)
```

Expected service selection: only `staging-db`, `gateway`, `frontend` (order
may vary). No connector, migration or initializer. Render success is not
host binding, connectivity or authentication acceptance.

## 2. Approved startup, preserving current data

Before issuing startup commands, verify clean Git/accepted revision, volumes
and project labels, staging secret check and port availability, as in the
[private procedure](RELEASE.md#private-staging-application-startup-procedure-only-not-run).
Do not rerun baseline, migrations or administrator bootstrap.

After separate startup/probe approval, repeat the complete render block above,
inserting the following before its closing `)`:

```bash
  for service in staging-db gateway frontend; do
    csa_shared_compose up --detach --wait --wait-timeout 120 \
      --no-deps --pull never "$service" || exit 1
  done
  csa_shared_compose ps --format '{{.Service}} {{.Status}}' || exit 1
  curl --noproxy '*' --silent --show-error --output /dev/null \
    --max-time 10 --write-out 'frontend HTTP %{http_code}\n' \
    http://127.0.0.1:18030/login
```

Require three healthy services and frontend HTTP 200. Repeat the private
gateway readiness and frontend-to-gateway checks from RELEASE using
`csa_shared_compose`, not the base-only helper. Confirm Docker's frontend
published binding is only `127.0.0.1:18030`; gateway/database have none.
Services persist after success; failed startup may leave earlier services
running. Stop on failure without disabling hardening or broadening bindings.

## 3. Prepare the live Caddy edit (separate approval)

Do not install a new Caddy service, replace the full file with this snippet,
or change existing sites. The target is the existing
`/etc/caddy/Caddyfile`, not a repository file. Before editing, obtain approval
and confirm no other operator is changing it.

Create a uniquely named backup, refusing a collision:

```bash
sudo test ! -e /etc/caddy/Caddyfile.before-csa-staging &&
sudo cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-csa-staging
```

If the backup already exists, stop and reconcile its origin; do not overwrite
it. Then use:

```bash
sudoedit /etc/caddy/Caddyfile
```

Append **only** the contents of `deploy/staging-console.Caddyfile` as a separate
site block, leaving existing blocks intact. If the staging site already exists,
stop rather than add a duplicate or replace unknown work. Do not add the old
development ingress matcher or proxy the gateway directly.

Review the exact file difference locally, then validate the full file:

```bash
sudo diff -u /etc/caddy/Caddyfile.before-csa-staging /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

`diff` normally exits 1 for the intended addition; read its scope, not just
the exit code. Never paste unrelated configuration/secrets into chat.
Only a successful full validation and expected staging-only diff permit a
separately approved reload:

```bash
sudo systemctl reload caddy &&
systemctl is-active caddy
```

This reloads the shared Caddy service and can affect its other sites; no
Cloudflare connector restart is required. Do not use restart as a fallback.
The snippet alone was locally validated; that does not prove the full host
configuration, reload or existing websites are healthy.

After reload/probe approval:

```bash
curl --noproxy '*' --silent --show-error --output /dev/null \
  --max-time 10 --write-out 'Caddy staging HTTP %{http_code}\n' \
  --header 'Host: staging-console.fourstacks.my' \
  http://127.0.0.1/login
```

Require 200. If it is 403, check actual loopback source/topology before changing
the restriction; if 502, verify frontend binding/readiness. Do not expose the
frontend more widely to fix either error. Verify pre-agreed existing-site
checks with owner approval without touching their routes.

## 4. Approved exact-host route on the existing tunnel

Only after the safeguard gates and local Caddy check pass, obtain approval
for this specific public route/DNS change. In **Networking > Tunnels**, choose
the existing shared host tunnel and add **only**:

| Field | Value |
| --- | --- |
| Hostname | `staging-console.fourstacks.my` |
| Path | Empty |
| Service type | HTTP |
| Service URL | `127.0.0.1:80` |
| HTTP Host Header override | Leave unset; preserve original staging hostname |

The effective origin is `http://127.0.0.1:80`, **not**
`http://frontend:3000`: this connector runs on the host, outside Docker DNS.
If an inherited Host override exists, stop and review it at route scope;
never change shared defaults blindly. Do not route to the landing-page app,
host port 3000, or gateway. No new tunnel/token/Access configuration is needed.

The published route can create an explicit DNS record for staging that
supersedes wildcard coverage for this hostname only. Do not remove the wildcard
or alter apex/www. If the dashboard reports an existing exact record or changes
to other routes, cancel. Public routing is exposure, not merely a connectivity
check; do not enable before approval.

## 5. Acceptance and safe withdrawal

Use the [public acceptance matrix](STAGING_INGRESS.md#acceptance-matrix-no-checks-executed-by-this-document)
and source contracts, interpreted for shared Caddy instead of dedicated
connector topology. Public TLS must be trusted; anonymous login-page delivery
is allowed, protected API/pages must deny/redirect, owner first login must
require password change, CSRF and secure cookies must work, and logout must
revoke authority. Caddy's local HTTP hop means forwarded-protocol behaviour
must be tested; do not disable secure cookies, bypass CSRF, or trust arbitrary
forwarded headers to hide failures. No HAR/cookie/password exports.

For this owner-presented demo, public tenant/role acceptance is still an
unmet product gate; do not provision visitor identities or claim it complete.
Application/DB failure drills and persistent backup/restore also remain.
Do not stop the shared tunnel or Caddy as a CSA drill or off switch.

Withdrawal, with separate approval:

1. Remove only the new staging published route and reconcile only its exact
   staging DNS record. Preserve shared tunnel and all other routes. Wildcard
   coverage may resume; disappearance of DNS is not expected.
2. Stop only frontend, gateway, then staging database, preserving both volumes:

```bash
docker container stop csa-staging-frontend-1 csa-staging-gateway-1 &&
docker container stop csa-staging-staging-db-1
```

3. To withdraw the Caddy site, remove only its staging block, review and
   validate the full file, then separately approve a reload. Do not blindly
   restore the entire backup if any unrelated edit occurred since it was made.
4. Confirm the staging CSA mapping is withdrawn and pre-agreed existing-site
   checks still pass. Do not delete volumes or credentials; do not uninstall
   Caddy or cloudflared.

Keep the backup until closeout; deleting it is not part of this proposal.
Batch sanitized outcomes/failures into one evidence update. Full hosted CI
remains the merge gate, and executable CD still requires a proven deployment/
recovery and backup process. Public login reachability is not production
readiness or completed staging CD.
