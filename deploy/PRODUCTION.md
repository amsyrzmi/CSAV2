# Production stack (interim single host)

How the production Compose stack differs from staging, how a commit reaches it, and how the owner
sets the host up once. The designs and the reasons are in
`docs/superpowers/specs/2026-11-04-lightsail-s3-interim-hosting-design.md` (the stack) and
`docs/superpowers/specs/2026-11-10-production-deploy-design.md` (the deploy). This file is the
operator's half. The repository is public: every value that identifies the deployment (bucket,
region, hostnames, account, instance, role ARNs) is written here as a placeholder such as
`<bucket>`, `<region>`, `https://<console-host>` or `<managed-instance-id>`, and lives only on the
instance, in AWS or in GitHub secrets.

The production stack is the three staging Compose files plus `deploy/compose.production.yml`,
applied last. The overlay changes only:

- files (knowledge base, HR and training library) live in S3 (`STORAGE_BACKEND=s3`) and no upload volume is mounted;
- two workers, one training worker (video jobs), and memory limits and OOM priorities so the
  kernel kills the training worker, then a worker or the frontend, before Postgres;
- Postgres settings for a 2 GB host;
- the gateway joins the `edge` network so the tunnel connector can reach it;
- the database image can be named by the operator (`CSA_PROD_DB_IMAGE`) while the registry package
  is private.

`tests/test_production_overlay.py` renders the stack with synthetic values and checks all of this
against the real settings class. The memory numbers are starting values; the capacity gate decides
whether they hold.

**The rule:** production only receives a commit that staging has already run. The `deploy-staging`
workflow rolls every green merge out to staging and, after the instance reported success, records
the commit as a successful GitHub Deployment in the environment `staging`, described
`staging rollout`; the production rollout refuses any commit without that record, and only a
deployment with that description counts (GitHub's own auto-created deployments never do). The one
exception is the owner's break-glass procedure below, which is outside that rule.

## Deploying from GitHub

Moved to deploy/DEPLOY.md, "Production: deploying from GitHub". That section holds how the workflow
works, the steps from the plan to the result, and what a smoke stop, a failure and each refusal mean;
this file keeps what is specific to this host:

- the root-owned entry script `/usr/local/sbin/csa-prod-deploy` runs as `ubuntu`, in the checkout
  `/home/ubuntu/CSAV2`, with `/opt/csa-deploy/venv/bin/python`;
- every run is logged in the directory `/var/log/csa-prod-deploy`;
- the rollout core takes the per-target lock `/run/lock/csa-prod-rollout.lock`, shared with break-glass
  runs by hand (see "Reinstalling the shared entry script").

### Optional registry login on the instance

The rollout pulls through a temporary, private
Docker config. If `/srv/csa-prod/registry/config.json` exists, the rollout copies it into that
config (mode 0600, removed with it when the run ends) before any pull, so the pulls use its
login; without the file the pulls stay anonymous, as today. It holds a read-only registry login
(`read:packages` only). The file must contain ONLY an `auths` object whose entries each have
an inline `auth` value. Do not copy a `docker login` config: anything with `credsStore`, `credHelpers`, `proxies` or
other keys, or an entry without an inline `auth`, makes the rollout stop with
`Stopped at plan: registry auth file <path> is not usable`. The file must be mode 0600; a
symlink, FIFO, directory or a file over 64 KiB is refused. The content is never printed. If the
copy itself fails (for example a full disk) the run stops with `could not copy the registry auth
file <path>`. Never commit it. Create it without echoing the token, as the deploy user (`ubuntu`), who owns
the directory and the file:

```
sudo install -d -m 0700 -o ubuntu -g ubuntu /srv/csa-prod/registry
(
  umask 077
  read -rsp "registry read token: " T; echo
  rm -f /srv/csa-prod/registry/config.json
  printf '{"auths":{"ghcr.io":{"auth":"%s"}}}' "$(printf '%s:%s' <github-user> "$T" | base64 -w0)" > /srv/csa-prod/registry/config.json
)
```

It is needed only once the images are private.

## One-time setup

The scripted path is in `deploy/DEPLOY.md`, "One-time setup"; the steps below are the manual
equivalents and the reference the scripts' tests compare against.

The owner does this once, after the deploy PR is merged to `main`. Each step names where it runs.
`<region>` is the one AWS region used for production; the activation, the two documents, the two
role policies and `PROD_DEPLOY_REGION` must all use it. Nothing in this section is ever pasted
into the repository or a chat; secret values go in at a hidden prompt.

`deploy/aws/setup-target.sh production check --region <region> --node <managed-instance-id>
--subject '<subject>'` (CloudShell, read-only) verifies steps 3 and 4 below: the OIDC provider,
each role's trust and inline policy and each document's default version, and, through the node's
ping status, step 2 (registration). It never verifies step 1: the node role is touched only by
`activate`. It reports
`ok`, `missing` or `differs` per item and exits 0 only when everything is `ok`. It does not
replace reading the steps, and never run its `apply` mode against production without the owner's
decision. `--subject` may be repeated, and production's live roles trust several subjects, so
`production check` must be given all of them (and `production apply` is never run with fewer).
`production apply` refuses to remove a subject that a live role trusts unless it is given
`--allow-subject-removal`. `apply` replaces the whole trust document of a role, not only its
subject: any extra statement or condition in a live trust is overwritten, and `check` reports it
as `differs`.

### 1. AWS (CloudShell, admin login): the agent's service role and an activation

The Systems Manager agent on the instance (a hybrid node, not EC2) gets its AWS credentials from
this service role. It needs only the managed policy `AmazonSSMManagedInstanceCore`.

```bash
REGION=<region>
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
cat > ssm-node-trust.json <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ssm.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"$ACCOUNT"},"ArnEquals":{"aws:SourceArn":"arn:aws:ssm:$REGION:$ACCOUNT:*"}}}]}
EOF
aws iam create-role --role-name csa-prod-ssm-node --assume-role-policy-document file://ssm-node-trust.json
aws iam attach-role-policy --role-name csa-prod-ssm-node --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws ssm create-activation --region "$REGION" --iam-role csa-prod-ssm-node \
  --default-instance-name csa-prod --registration-limit 1 \
  --expiration-date "$(date -u -d '+4 hours' +%Y-%m-%dT%H:%M:%SZ)"
```

The last command prints an `ActivationId` and an `ActivationCode`: one registration, valid for
four hours. Keep the CloudShell tab open for steps 3 and 4.

Keep this role narrow: any root process on the instance can use its credentials. If command
output is ever sent to S3 or CloudWatch Logs, give the role write access to that one bucket prefix
or log group only, never wider S3 or CloudWatch access.

### 2. Instance (SSH as `ubuntu`): install and register the agent

```bash
snap list amazon-ssm-agent || sudo snap install amazon-ssm-agent --classic
sudo snap stop amazon-ssm-agent
read -rp "Activation id: " ACT_ID
read -rsp "Activation code: " ACT_CODE; echo
sudo /snap/amazon-ssm-agent/current/amazon-ssm-agent -register -id "$ACT_ID" -code "$ACT_CODE" -region <region>
unset ACT_ID ACT_CODE
sudo snap start amazon-ssm-agent
```

The code is typed at a hidden prompt, so it never lands in shell history; it is single-use,
expires with the activation, and appears briefly in `ps` and in sudo's entry in the auth log while
the command runs. If the agent asks
whether to overwrite an existing registration, answer yes. Then measure it on this 2 GB host:

```bash
ps -eo rss=,args= | grep -E '[s]sm-agent'
free -m
```

The first column is resident memory in KB. Stop and rethink if the agent uses more than 100 MB resident or `free` shows less than 250 MB
available. In CloudShell, find the managed node id (it starts with `mi-`) and check it is
`Online`, then delete the used activation:

```bash
aws ssm describe-instance-information --region "$REGION" \
  --query 'InstanceInformationList[].[InstanceId,PingStatus,ComputerName]' --output table
NODE=<managed-instance-id>
aws ssm delete-activation --region "$REGION" --activation-id <ActivationId from step 1>
```

### 3. AWS (CloudShell): the GitHub OIDC provider and the two deploy roles

The plan role trusts only the `production-plan` environment of this repository and may send only
the plan document; the apply role trusts only `production` and may send only the apply document.
Both may send to the one instance only, and neither has any other AWS access.
If the roles already trust several subjects, do not run the manual commands below as written (they
fill one subject): use `deploy/aws/setup-target.sh production check` and `apply` with every subject instead.

The trust policies name the token's subject, and GitHub decides its shape per repository: the classic
`repo:<owner>/<repo>` or, for repositories that use the immutable subject, `repo:<owner>@<owner id>/<repo>@<repo id>`.
Ask GitHub which one this repository issues, on a machine where `gh` is logged in as a repository
admin, for example your own machine (the endpoint needs the admin login):

```bash
gh api repos/amsyrzmi/CSAV2/actions/oidc/customization/sub --jq .sub_claim_prefix
```

Copy the printed value into CloudShell as `SUBJECT=<printed value>`, in the same tab as the commands
below. A wrong value is the usual cause of `Not authorized to perform sts:AssumeRoleWithWebIdentity`
at the workflow's AWS step (see `deploy/DEPLOY.md`, "Production: deploying from GitHub", "What a failure means").

```bash
aws iam list-open-id-connect-providers
SUBJECT=<printed value>   # e.g. the prefix printed above, never written into the repository
# Only if token.actions.githubusercontent.com is not listed:
aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com --client-id-list sts.amazonaws.com
OIDC=arn:aws:iam::$ACCOUNT:oidc-provider/token.actions.githubusercontent.com
mkdir -p ~/csa-deploy && cd ~/csa-deploy
RAW=https://raw.githubusercontent.com/amsyrzmi/CSAV2/main/deploy/production
for f in iam/plan-trust-policy.json iam/plan-permissions-policy.json iam/apply-trust-policy.json iam/apply-permissions-policy.json ssm-document-plan.json ssm-document-apply.json; do
  curl -fsSLO "$RAW/$f"
done
: "${REGION:?}" "${ACCOUNT:?}" "${NODE:?}" "${OIDC:?}" "${SUBJECT:?}" && sed -i -e "s|<region>|$REGION|g" \
  -e "s|<account-id>|$ACCOUNT|g" -e "s|<managed-instance-id>|$NODE|g" -e "s|<oidc-provider-arn>|$OIDC|g" -e "s|<repo-subject>|$SUBJECT|g" ./*-policy.json
grep -n '<' ./*-policy.json || echo "placeholders filled"
aws iam create-role --role-name csa-prod-deploy-plan --assume-role-policy-document file://plan-trust-policy.json
aws iam put-role-policy --role-name csa-prod-deploy-plan --policy-name send-plan-document --policy-document file://plan-permissions-policy.json
aws iam create-role --role-name csa-prod-deploy-apply --assume-role-policy-document file://apply-trust-policy.json
aws iam put-role-policy --role-name csa-prod-deploy-apply --policy-name send-apply-document --policy-document file://apply-permissions-policy.json
```

The `:` guard stops the `sed` when `REGION`, `ACCOUNT` or `NODE` is empty, as in a new CloudShell
tab (set them again from steps 1 and 2). If the `grep` line prints anything other than
`placeholders filled`, stop before creating the roles. The filled copies stay in CloudShell, never
in the repository. The two role ARNs
(`aws iam get-role --role-name csa-prod-deploy-plan --query Role.Arn --output text`, and the same
for `csa-prod-deploy-apply`) go into GitHub secrets in step 6.

### 4. AWS (CloudShell): the two command documents

```bash
aws ssm create-document --region "$REGION" --name csa-production-plan --document-type Command --document-format JSON --content file://ssm-document-plan.json
aws ssm create-document --region "$REGION" --name csa-production-apply --document-type Command --document-format JSON --content file://ssm-document-apply.json
```

`csa-production-plan` comes from `deploy/production/ssm-document-plan.json` (`sha`, `pythonDigest`,
`frontendDigest`) and `csa-production-apply` from `deploy/production/ssm-document-apply.json`
(`sha`, `migrations`, `hostDigest`, `pythonDigest`, `frontendDigest`). Every parameter has an `allowedPattern`, so Systems Manager rejects anything else,
and each document runs `runuser -u ubuntu -- /usr/local/sbin/csa-prod-deploy` with its action
written in. When a PR changes a document file, update it the same way:
`aws ssm update-document --name <name> --content file://<file> --document-version '$LATEST'`,
then `aws ssm update-document-default-version --name <name> --document-version <new version>`.
The documents and the instance's entry script change together: see "After the digest change merges".

### 5. Instance (SSH as `ubuntu`): checkout, tools, entry script, host file, backup, GitHub read

Deploys run as `ubuntu` in the dedicated checkout `/home/ubuntu/CSAV2` and must not need `sudo`.

a. The checkout must be clean and fetch the public repository over HTTPS:

   ```bash
   git -C ~/CSAV2 remote get-url origin
   git -C ~/CSAV2 status --porcelain --untracked-files=no
   git -C ~/CSAV2 status --porcelain --untracked-files=all -- db/migrations
   git -C ~/CSAV2 fetch origin main
   git -C ~/CSAV2 checkout --detach origin/main
   ```

   The remote must be `https://github.com/amsyrzmi/CSAV2.git` and both `status` lines must print
   nothing (the rollout refuses tracked changes and untracked migration files).

b. Tools and permissions. `ubuntu` must be in the `docker` group, and `python3` must be 3.12
   (the version the project pins; Ubuntu 24.04 ships it). Stop if it is not:

   ```bash
   id -nG | tr ' ' '\n' | grep -x docker
   python3 --version
   sudo apt-get install -y python3-venv gh
   ```

c. The rollout's Python environment, root-owned, at `/opt/csa-deploy/venv` (the path the entry
   script runs), from `deploy/production/requirements.txt`. Re-run the `pip` line when that file
   changes.

   ```bash
   sudo install -d -m 0755 -o root -g root /opt/csa-deploy
   sudo python3 -m venv /opt/csa-deploy/venv
   sudo /opt/csa-deploy/venv/bin/pip install --no-cache-dir -r ~/CSAV2/deploy/production/requirements.txt
   /opt/csa-deploy/venv/bin/python -c "import psycopg; print(psycopg.__version__)"
   ```

d. The entry script, installed root-owned outside the checkout (a deploy cannot change the program
   Systems Manager runs), and its log directory. The script reads a root-owned config file next
   to it and exits 78 without one, so install the config first (see Reinstalling the shared
   entry script), then the script. Reinstall the script the same way whenever a PR changes
   `deploy/production/deploy_entry.sh`.

   ```bash
   sudo install -m 0755 -o root -g root ~/CSAV2/deploy/production/deploy_entry.sh /usr/local/sbin/csa-prod-deploy
   sudo install -d -m 0750 -o ubuntu -g ubuntu /var/log/csa-prod-deploy
   ```

e. The host file `/srv/csa-prod/host.env`, readable by `ubuntu`. It holds what the public
   repository must not: one `KEY=value` per line, no quotes, no spaces, `#` comments allowed.
   Copy the values from `~/csa-prod-env.sh`.

   ```bash
   [ -e /srv/csa-prod/host.env ] || sudo install -m 0600 -o ubuntu -g ubuntu /dev/null /srv/csa-prod/host.env
   nano /srv/csa-prod/host.env
   ```

   ```text
   CSA_PROD_S3_BUCKET=<bucket>
   CSA_PROD_S3_REGION=<region>
   CSA_STAGING_CONSOLE_ORIGIN=https://<console-host>
   CSA_STAGING_HOOKS_ORIGIN=https://<hooks-host>
   CSA_PROD_DB_IMAGE=<the local database image tag from ~/csa-prod-env.sh>
   ```

   The first four are required. `CSA_PROD_DB_IMAGE` is needed while the instance runs a local
   database image instead of the registry default. The only other keys allowed are the
   `CSA_PROD_*` tuning keys in "Tuning without a code change" below. The six switches belong in
   `deploy/production-rollout.env` (a reviewed PR), and `CSA_PROD_GROQ_CHAT_URL` is never
   accepted. The optional Jev switches (`CSA_STAGING_JEV_SECRET`, `CSA_STAGING_ASSISTANT_DECIDER_*`,
   `CSA_STAGING_CUSTOMER_DECIDE_MODE_DEFAULT`, `CSA_STAGING_JEV_FLOOR_*`) may be added to
   `deploy/production-rollout.env` by a reviewed PR too; today that file sets the four shadow lines
   in "Jev on production (shadow)" below and nothing else of the Jev switches. The other optional
   switches are `CSA_STAGING_TRAINING_VIDEO` ("Training video" below) and
   `CSA_STAGING_HR_DEFAULT_TEMPLATES` ("HR default templates" below); both are off when unset.
   `grep -n "[\"']" /srv/csa-prod/host.env` must print nothing.

f. The backup script, **only after step e**: the repository's `deploy/production/backup.sh` reads
   the bucket and region from `host.env`, so if it replaced the installed copy first, the
   6-hourly cron backup would fail. Backups run as `ubuntu` without `sudo`: `ubuntu` must be able
   to write `/var/tmp/csa-backup.lock`, append to `/srv/csa-prod/backup/backup.log`, read
   `/srv/csa-prod/backup/s3-key.txt` and `host.env`, and use docker. Check, install, then run it
   once by hand. `install` replaces the file, so the directory `/srv/csa-prod/backup` itself must
   be writable by `ubuntu`, and every later reinstall the deploy asks for runs without `sudo`. If
   `ls -ld` shows another owner, or the installed `backup.sh` is root-owned (older copies were
   installed that way), run once
   `sudo chown ubuntu:ubuntu /srv/csa-prod/backup /srv/csa-prod/backup/backup.sh`, then the plain
   `install` below:

   ```bash
   ls -ld /srv/csa-prod/backup
   ls -l /var/tmp/csa-backup.lock /srv/csa-prod/backup/
   crontab -l | grep backup.sh
   cd ~/CSAV2 && install -m 0755 deploy/production/backup.sh /srv/csa-prod/backup/backup.sh
   /srv/csa-prod/backup/backup.sh
   tail -n 1 /srv/csa-prod/backup/backup.log
   ```

   The files must be owned by `ubuntu`, the cron line must be in `ubuntu`'s crontab, and the last
   log line must be a new `<UTC timestamp> ok <N> bytes`. If it says
   `skipped: previous backup still running`, wait a minute and run it again. Every deploy checks
   that the installed copy is byte-for-byte the commit's `deploy/production/backup.sh`.

g. GitHub read access for `gh`, **for break-glass only**. A deploy started from the workflow
   passes the image digests and needs no token on the instance. The `read:packages` token is needed only for break-glass:
   a host run by hand with no digests looks them up with `gh` (GitHub's package API needs a
   token even for public packages) and reads the `staging` deployments, so the staged gate still
   holds. Keep the token on the instance until production has run a commit after the digest
   change that you would roll back to: an older revision can only be restored this way. The
   token comes from the other developer: a classic token with only the `read:packages`
   scope. `gh auth login
   --with-token` insists on wider scopes, so write `gh`'s host file directly, with the token typed
   at a hidden prompt:

   ```bash
   install -d -m 0700 ~/.config/gh
   read -rsp "GitHub read token: " T; echo
   ( umask 077; printf 'github.com:\n    oauth_token: %s\n    user: %s\n    git_protocol: https\n' "$T" "<token owner's GitHub login>" > ~/.config/gh/hosts.yml )
   unset T
   gh auth status
   gh api "/users/amsyrzmi/packages/container/csa-python/versions?per_page=1" --jq '.[0].name'
   ```

   The last line must print a `sha256:` digest. No `docker login` is needed: the rollout pulls
   the public images by digest with its own temporary Docker config, so it would not use one
   (for private images it reads the optional registry login file, see Deploying from GitHub).

h. Run a plan on the instance as Systems Manager would, for the newest staged commit. It gives
   no digests, so it takes the break-glass path and needs the token from step g:

   ```bash
   SHA=$(gh api "repos/amsyrzmi/CSAV2/deployments?environment=staging&per_page=1" --jq '.[0].sha')
   sudo runuser -u ubuntu -- /usr/local/sbin/csa-prod-deploy plan "$SHA" "" ""
   ```

   It must end with the apply command line and no `Stopped at` line. Whenever you test the entry
   script by hand, run it as `sudo runuser -u ubuntu -- /usr/local/sbin/csa-prod-deploy ...`,
   never as root directly: a root run leaves the lock file root-owned, and every later run then
   fails at the lock with `Permission denied`.

### 6. GitHub (repository Settings, as `amsyrzmi`): two environments, four secrets, two variables

- Environment **`production-plan`**: no required reviewer. Deployment branches and tags:
  "Selected branches and tags", one rule for the branch `main`. Environment secrets:
  `PROD_PLAN_ROLE_ARN` (the `csa-prod-deploy-plan` role ARN) and `PROD_INSTANCE_ID`
  (`<managed-instance-id>`).
- Environment **`production`**: Required reviewers `amsyrzmi` and `DanSan0408` (one approval is
  enough; `DanSan0408` needs at least read access to the repository, which they have). Leave
  "Prevent self-review" off. Deployment branches and tags: only `main`, as above. Environment
  secrets: `PROD_APPLY_ROLE_ARN` (the `csa-prod-deploy-apply` role ARN), `PROD_INSTANCE_ID` and
  `PROD_CONSOLE_ORIGIN` (the production console origin: `https://` plus the hostname, no path,
  port or trailing slash; the public smoke test asks it for `/`).
- Repository variable **`PROD_DEPLOY_REGION`**: `<region>`.
- Repository variable **`IMAGE_NAMESPACE`**: `ghcr.io/amsyrzmi`, the lowercase registry prefix of the
  images (see `deploy/DEPLOY.md`, "Production: deploying from GitHub"); the owner changes it if the
  repository moves.

On both environments leave "Allow administrators to bypass" off (the setup script and its `--check`
want `can_admins_bypass` to be `false` on `production` and `production-plan`).

The `main`-only restriction on both environments is mandatory: the roles' trust policies name the
environment, and the environment's branch rule is what keeps another branch from getting that
token (the jobs also check `github.ref`). The role ARNs, instance id and console origin are
secrets so the public workflow logs mask them, and the real values never go into the repository.

Create the two environments, their reviewers and branch rules first, in the web settings
(Settings, Environments); `gh secret set --env` for an environment that does not exist yet fails
with HTTP 404. Then set the secrets and the two variables (`PROD_DEPLOY_REGION` and
`IMAGE_NAMESPACE`), in the web settings or from a terminal, each secret typed at `gh`'s hidden prompt:

```bash
gh secret set PROD_PLAN_ROLE_ARN --repo amsyrzmi/CSAV2 --env production-plan
gh secret set PROD_INSTANCE_ID --repo amsyrzmi/CSAV2 --env production-plan
gh secret set PROD_APPLY_ROLE_ARN --repo amsyrzmi/CSAV2 --env production
gh secret set PROD_INSTANCE_ID --repo amsyrzmi/CSAV2 --env production
gh secret set PROD_CONSOLE_ORIGIN --repo amsyrzmi/CSAV2 --env production
gh variable set PROD_DEPLOY_REGION --repo amsyrzmi/CSAV2 --body <region>
gh variable set IMAGE_NAMESPACE --repo amsyrzmi/CSAV2 --body ghcr.io/amsyrzmi
```

### 7. GitHub: verify with a plan

Start `deploy-production` with an empty `sha` (`deploy/DEPLOY.md`, "Production: deploying from GitHub", step 2) and read the plan.
On the first run it must show `Target SHA:` for the tip of `main`, both images by digest, the rollback references as
`unknown (a local image with no registry digest, or not running)`, and the pending migrations.
Optionally check, on the instance, that the image pulls without credentials (the plan's
`python:` digest; the pull is reused by the apply):

```bash
D=$(mktemp -d); DOCKER_CONFIG="$D" docker pull ghcr.io/amsyrzmi/csa-python@<digest from the plan>; rm -rf "$D"
```

Then reject the `production` deployment, or approve it to make the first deploy, watched live.

### First production deploy: what to check

- The first plan's log (`/var/log/csa-prod-deploy/`) shows no `getcwd` or permission lines before
  `Target SHA:`, and the instance makes no `gh` call (the digests came from the workflow, so no
  `gh auth login` prompt or HTTP 401 can appear).
- Before approving: an anonymous pull of the plan's digests works from the instance (the
  `docker pull` with an empty `DOCKER_CONFIG` in step 7, for both the `python:` and the
  `frontend:` digest).
- The first apply with no pending migrations is accepted by `send-command` (the runner leaves
  the empty `migrations` parameter out; the document's default is empty).
- The Systems Manager agent's resident memory (`ps -o rss,cmd -C amazon-ssm-agent,ssm-agent-worker`) and the
  headroom `free -m` shows are still acceptable during and after the run.
- After the first run the lock file and the log directory are owned by `ubuntu`:
  `ls -l /var/lock/csa-prod-deploy.lock` and `ls -ld /var/log/csa-prod-deploy`.
- The two pinned action SHAs in `.github/workflows/deploy-production.yml` are the tagged
  releases their comments claim: `gh api repos/<owner>/<action>/git/ref/tags/<tag>` (for
  example `repos/actions/checkout/git/ref/tags/v7.0.1`) must name the pinned SHA; when its
  `object.type` is `tag`, follow it with `gh api repos/<owner>/<action>/git/tags/<object sha>`.

## After the digest change merges

Once the pull request that moves the digest lookup into the workflow is merged to `main`, the
instance's entry script and the two command documents must be updated together, and the new
secret set, before the next run. The workflow now sends `pythonDigest` and `frontendDigest` to
both documents, and its apply job refuses to start without `PROD_CONSOLE_ORIGIN`. The entry
script and the documents must match, so update both before the next run. An old document sent
by the new workflow is rejected by Systems Manager, which does not know the digest parameters (the run fails closed). A
new entry script with an old document takes the break-glass path (the `gh` lookup) instead of
failing. A new document sent to an old entry script is refused by the old script's argument-count
check. Do the five steps in this order. `<name>` is
`csa-production-plan` and then `csa-production-apply`; `<file>` is the matching
`ssm-document-*.json` under `~/csa-deploy`.

1. **Update both command documents** (CloudShell). First download the merged files, then run the
   two `aws` commands once for each document. `update-document` prints the new version number;
   use it in the next command for that document. Add `--region "$REGION"` if CloudShell's default
   region is not the production one. The `grep` must print a non-zero count for each file. If it
   prints 0, `raw.githubusercontent.com` has not caught up with the merge yet: wait a few minutes,
   download again and re-check before running the `aws` commands.

   ```bash
   mkdir -p ~/csa-deploy && cd ~/csa-deploy
   RAW=https://raw.githubusercontent.com/amsyrzmi/CSAV2/main/deploy/production
   curl -fsSLO "$RAW/ssm-document-plan.json" && curl -fsSLO "$RAW/ssm-document-apply.json"
   grep -c pythonDigest ssm-document-plan.json ssm-document-apply.json
   aws ssm update-document --name <name> --content file://<file> --document-version '$LATEST'
   aws ssm update-document-default-version --name <name> --document-version <new version>
   ```

2. **Reinstall the entry script** (Instance, SSH as `ubuntu`). The checkout `~/CSAV2` on the
   instance must be on the merged `main` first (`git -C ~/CSAV2 fetch origin main` and
   `git -C ~/CSAV2 checkout --detach origin/main`), because the script is installed from it.
   The script exits 78 without its config, so install the config first (see Reinstalling the
   shared entry script), then the script:

   ```bash
   sudo install -m 0755 -o root -g root ~/CSAV2/deploy/production/deploy_entry.sh /usr/local/sbin/csa-prod-deploy
   ```

3. **Set the new secret** (a machine with `gh` logged in as a repository admin). The value is the
   production console origin and is typed at `gh`'s hidden prompt.

   ```bash
   gh secret set PROD_CONSOLE_ORIGIN --repo amsyrzmi/CSAV2 --env production
   ```

4. **Check the two origins** (Instance, SSH as `ubuntu`, then the owner's machine). The smoke
   test asks both origins in `host.env` for an answer below 500, and a 5xx fails the smoke test
   and, on an image-only deploy, rolls the deploy back. These checks have not yet run against
   the real origins, so run them first. On the instance this requests them exactly as the smoke
   test does (the same curl options, `--max-time 15`) and prints only the key name and the
   status code, never an origin:

   ```bash
   ( set -a; . /srv/csa-prod/host.env; set +a
     for K in CSA_STAGING_CONSOLE_ORIGIN CSA_STAGING_HOOKS_ORIGIN; do
       printf '%s ' "$K"
       curl --silent --output /dev/null --write-out '%{http_code}\n' --max-time 15 --url "${!K}"
     done )
   ```

   Each line must end in three digits below 500 (`000` means curl got no answer). From outside
   (CloudShell or the owner's machine), the public smoke test's check of the console origin must
   give 2xx or 3xx:

   ```bash
   curl --silent --output /dev/null --write-out '%{http_code}\n' --max-time 15 https://<console-origin>/
   ```

   On a 5xx, `000` or any other status, do not start the run: fix the tunnel or the route first.
5. **Start a run** (the same machine as step 3) and read the plan. Reject any run still waiting
   for approval from before the change merged, or this one queues behind it. A plan for a tip
   that is already deployed is harmless: it shows nothing to change, and a failure cannot roll
   back onto identical images.

   ```bash
   gh workflow run deploy-production --repo amsyrzmi/CSAV2 --ref main
   ```

The first run after this should show the two digests in the plan from the registry and, on apply,
`smoke ...: ok` lines followed by `Rollout of <sha7> finished.`.

## Reinstalling the shared entry script

`deploy/production/deploy_entry.sh` is shared by production and staging. It reads a config file
installed next to it, as `/usr/local/sbin/csa-prod-deploy.conf` on this instance, and refuses
with exit 78 (a configuration error) when the file is missing, is not a regular file, is not owned
by the script's owner, is writable by group or others, or has an unknown, repeated or missing key.
Environment variables override nothing. The config has exactly six keys, all required, as
`KEY=value` lines (it is parsed, never sourced; nothing in it is a secret). Today's production
values are in `deploy/production/deploy.conf.example`:

```text
TARGET=production
DEPLOY_USER=ubuntu
CHECKOUT=/home/ubuntu/CSAV2
PYTHON=/opt/csa-deploy/venv/bin/python
LOCK=/var/lock/csa-prod-deploy.lock
LOG_DIR=/var/log/csa-prod-deploy
```

Install the config first, then the script, on the instance as `ubuntu`, from the checkout
`~/CSAV2` at the merged `main`:

```bash
sudo install -m 0644 -o root -g root ~/CSAV2/deploy/production/deploy.conf.example /usr/local/sbin/csa-prod-deploy.conf
sudo install -m 0755 -o root -g root ~/CSAV2/deploy/production/deploy_entry.sh /usr/local/sbin/csa-prod-deploy
```

The installed `csa-prod-deploy` keeps working until it is replaced, so this is not a prerequisite
for anything else; it is needed the next time the entry script changes. A deploy also takes the
rollout core's own lock, `/run/lock/csa-prod-rollout.lock`, in `plan` and `apply`. A break-glass
run by hand takes it as well, so two rollouts of production cannot run at once, whoever starts
them.

## Restoring a backup

Every deploy takes a backup before it changes anything and prints
`Backup taken: backups/csa_staging-<timestamp>.dump`; the recovery text after a stop names the
same object. The 6-hourly cron backups have the same name form, and each `ok` line in
`/srv/csa-prod/backup/backup.log` starts with the timestamp. Restore into a scratch database
first, always; restoring over the live database is a separate, deliberate owner action.

**Getting the dump onto the instance.** The instance's backup key is put-only on `backups/`, so it
cannot read a dump back. Use the owner's admin login: download the object in the S3 console
(bucket `<bucket>`, folder `backups/`), or in CloudShell create a ten-minute link and fetch it on
the instance with the link typed at a hidden prompt (the link is a credential while it lasts):

```bash
# CloudShell:
aws s3 presign "s3://<bucket>/backups/csa_staging-<timestamp>.dump" --expires-in 600
# Instance, as ubuntu:
read -rsp "Download link: " U; echo
curl -fsS -o /var/tmp/csa-restore.dump "$U"; unset U
docker exec -i csa-prod-staging-db-1 pg_restore --list < /var/tmp/csa-restore.dump > /dev/null && echo "dump ok"
```

**1. Restore into a scratch database and check it** (this is the restore drill; it does not touch
the live data):

```bash
docker exec csa-prod-staging-db-1 psql -U csa -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE csa_restore_check OWNER csa"
docker exec -i csa-prod-staging-db-1 pg_restore -U csa -d csa_restore_check --exit-on-error --single-transaction < /var/tmp/csa-restore.dump
for DB in csa_restore_check csa_staging; do
  docker exec csa-prod-staging-db-1 psql -U csa -d "$DB" -tA -c "SELECT (SELECT count(*) FROM public.clients), (SELECT count(*) FROM public.messages), (SELECT count(*) FROM public.kb_chunks), (SELECT max(version) FROM schema_migrations)"
done
```

The two lines give clients, messages, knowledge-base chunks and the newest migration for the dump
and for the live database. For a drill they should be close (the live database has moved on since
the dump). Then drop the scratch database and delete the file:

```bash
docker exec csa-prod-staging-db-1 psql -U csa -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE csa_restore_check"
rm -f /var/tmp/csa-restore.dump
```

**2. Restore over the live database (owner only, deliberate).** This replaces everything written
since the backup. Do step 1 first and keep the dump. Then, as `ubuntu`:

1. Stop the services that write: `docker stop csa-prod-gateway-1 csa-prod-frontend-1 csa-prod-worker-1 csa-prod-worker-2 csa-prod-delivery-1 csa-prod-training-worker-1`.
2. Back up the current state first, so it can be recovered: `/srv/csa-prod/backup/backup.sh && tail -n 1 /srv/csa-prod/backup/backup.log`.
3. Replace the database, one line at a time, stopping if one fails:

   ```bash
   docker exec csa-prod-staging-db-1 psql -U csa -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS csa_staging WITH (FORCE)"
   docker exec csa-prod-staging-db-1 psql -U csa -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE csa_staging OWNER csa"
   docker exec -i csa-prod-staging-db-1 pg_restore -U csa -d csa_staging --exit-on-error --single-transaction < /var/tmp/csa-restore.dump
   ```

4. Compare the restored schema with the running images: the newest migration in the database
   (`docker exec csa-prod-staging-db-1 psql -U csa -d csa_staging -tA -c "SELECT max(version) FROM schema_migrations"`)
   and the newest file in the deployed commit (`ls ~/CSAV2/db/migrations/*.sql | tail -n 1`, after
   checking that `git -C ~/CSAV2 rev-parse HEAD` is the commit production runs). If
   they match, start the containers again with
   `docker start csa-prod-gateway-1 csa-prod-frontend-1 csa-prod-worker-1 csa-prod-worker-2 csa-prod-delivery-1 csa-prod-training-worker-1`.
   If the dump is older, do not start them: start `deploy-production` with `sha` set to the commit
   production runs, which applies the missing migrations and recreates the services (if that
   commit is older than the digest change, use break-glass instead, as in "Rolling back to a
   revision older than the digest change").
5. Check readiness (exit 0 and no output means ready; the gateway can take about 45 seconds):
   `docker exec csa-prod-gateway-1 python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=5)"`.
6. Delete the dump: `rm -f /var/tmp/csa-restore.dump`.

Uploaded files live in S3 and are not part of the dump.

## Break-glass: manual deploy (owner only)

This procedure is outside the staged-first rule: nothing checks that staging ran the commit. It
is for the owner only, for an urgent fix when staging or the deploy pipeline is unavailable. Use
`deploy-production` whenever it works. It uses the shell environment `~/csa-prod-env.sh` and its
`csa_compose` function (Manual shell environment, below); before using it, check that its switch
values match `deploy/production-rollout.env` and `/srv/csa-prod/host.env`, which the scripted
deploy uses instead.

Running the entry script on the instance by hand (`csa-prod-deploy plan` or `apply` with no image
digests) is a different, smaller exception: it does today's `gh` digest lookup, which needs the
`read:packages` token of One-time setup, step 5g, and it still enforces the staged gate. The
manual Compose procedure below does not.

1. Take a backup (as `ubuntu`, no `sudo`): `/srv/csa-prod/backup/backup.sh && tail -n 1 /srv/csa-prod/backup/backup.log`.
2. Bring the checkout to the commit: `git -C ~/CSAV2 fetch origin main && git -C ~/CSAV2 checkout --detach <full sha>`.
3. Load the environment and name the images by digest (from a plan, or from
   `gh api "/users/amsyrzmi/packages/container/csa-python/versions?per_page=100"`), then check the
   config:

   ```bash
   source ~/csa-prod-env.sh
   export CSA_STAGING_PYTHON_IMAGE=ghcr.io/amsyrzmi/csa-python@sha256:<digest>
   export CSA_STAGING_FRONTEND_IMAGE=ghcr.io/amsyrzmi/csa-frontend@sha256:<digest>
   csa_compose --profile dedicated-connector-approved config --quiet && echo "config ok"
   docker pull "$CSA_STAGING_PYTHON_IMAGE" && docker pull "$CSA_STAGING_FRONTEND_IMAGE"
   ```

4. Only if the commit has migrations the database lacks: stop the five services, then migrate.

   ```bash
   csa_compose stop gateway frontend worker delivery training-worker
   CSA_STAGING_MIGRATION_APPROVAL=approved-isolated-staging csa_compose --profile migration-approved run --rm --no-deps migrate
   ```

5. Recreate the services one by one, in this order, and read each result:

   ```bash
   for S in gateway worker delivery frontend training-worker; do
     csa_compose up --detach --wait --wait-timeout 300 --no-deps --pull never "$S" || break
   done
   ```

6. Check readiness with the probe in "Restoring a backup", step 5.

A commit deployed this way has no staged record, so a later `deploy-production` of the same commit
refuses until staging has run it.

### Rolling back to a revision older than the digest change

The workflow always sends image digests, and the entry script runs the target commit's own
rollout CLI with `--python-digest` and `--frontend-digest`. A commit from before the digest change
has no such options, so a workflow run for it fails at argument parsing (exit 2 with
`unrecognized arguments`) and changes nothing. That includes the revision production runs today,
before its first deploy after the change. Restore such a revision by hand on the instance, with no
digests, as `ubuntu` through the entry script (never as root, see One-time setup, step 5h):

```bash
sudo runuser -u ubuntu -- /usr/local/sbin/csa-prod-deploy plan <full sha> "" ""
sudo runuser -u ubuntu -- /usr/local/sbin/csa-prod-deploy apply <full sha> "<migrations>" "<host digest>"
```

Use the `Pending migrations:` and `Host settings digest:` values the plan printed (an empty
migrations argument for none). This path uses the `read:packages` token and still enforces the
staged gate, so the commit must have a staged record. Keep that token on the instance until
production has run a commit after the digest change that you would roll back to.

### Rolling back to the local images

Before the first scripted deploy the instance ran local image tags (`csa/python:prod-<sha7>`,
`csa/frontend:prod-<sha7>`), which plan and apply cannot bring back. If the first deploy stops
after the recreate and no migration was applied, the previous images are still on the host
(`docker image ls csa/python; docker image ls csa/frontend`) and `~/csa-prod-env.sh` still names
them:

```bash
source ~/csa-prod-env.sh
for S in gateway worker delivery frontend; do
  csa_compose up --detach --wait --wait-timeout 300 --no-deps --pull never "$S" || break
done
```

If the deploy being rolled back was the first to carry the training worker, those images predate
it (they have no `apps.training_worker`), so stop it rather than recreating it:
`csa_compose stop training-worker`.

Then check readiness as above. Do not delete the local tags until a scripted deploy has succeeded.
If migrations were applied, do not roll back; fix forward.

## Pruning old images

Step 0, before running anything: the script cannot see what production runs, so read it and pass it.
Find the commit SHA production runs now and the one it ran before (for example from the last two
`deploy-production` apply runs that succeeded, or from the image references on the instance; a failed
or unapproved run does not count, because a plan that was never approved or an apply that failed
names a SHA production never ran), and pass
both, each as `--production-sha <40-character SHA>`. The option is required. The staged
records only exist since about 2026-11-13, so images older than that have no record and are
protected only if you pass them.

Then run `python -m scripts.image_keep_list --scope users/<owner> --production-sha <current>
--production-sha <previous>` (add `--keep-newest N`, at least 1, or `--keep-sha <sha>` for extras).
It prints, for `csa-python` and `csa-frontend`, every registry version as `KEEP(newest)`,
`KEEP(production)`, `KEEP(staged)`, `KEEP(extra)`, `DELETE` or `REVIEW`. It only reads: it deletes
nothing, and it needs the `gh` login that already runs the rollout scripts. It keeps the newest 20
SHA-tagged versions, every version tagged with a production SHA, and every version tagged with a
SHA that one of the newest 15 staging deployments reached with a success status; it warns above the
tables when it finds no staged deployment, and above a package's table when a SHA you passed tags
no version of it (a typo). Only a SHA-tagged version outside that set is `DELETE`. A
version with no SHA tag (untagged, or only `latest` and the like) is `REVIEW`: it can be a child
manifest of an image index, so it is never proposed for deletion and the owner decides from its
digest and tags. The database image (`csa-pg16-postgis-vector`) is never listed, because it cannot
be rebuilt. The owner runs the deletion, from the `DELETE` rows, with a token that has
`delete:packages`; run the script again afterwards to check the result.

## Keys and who holds what

| Service | S3 key | Allowed |
| --- | --- | --- |
| gateway | application key (`csa-prod-app`) | `kb/`, `hr/` and `training/` read, write, delete; abort a multipart upload and list its parts under `training/`; `backups/` put-only |
| worker (two replicas) | worker key (`csa-prod-worker`) | `kb/` read only |
| training worker | training worker key (`csa-prod-training`), until made: the disabled placeholder | `training/` list, read, delete and abort uploads; no write |
| delivery, frontend, database | none | |

The worker only reads knowledge-base files, so a leaked worker key cannot read HR documents or
training files, or change anything. The policy is in `docs/OBJECT_STORAGE.md`. If the owner chooses not to create a
second key, the worker key files may hold a copy of the application key instead; the stack works
the same and the worker's narrower scope is lost.

## Secret files the overlay needs

Under the host's secret directory (`CSA_STAGING_SECRET_DIR`, mode `0400`, owner uid 1000), in a
`storage/` folder next to `runtime/` and `pipeline/`:

- `storage/s3_app_access_key_id`, `storage/s3_app_secret_access_key`
- `storage/s3_worker_access_key_id`, `storage/s3_worker_secret_access_key`
- `storage/s3_training_access_key_id`, `storage/s3_training_secret_access_key`: only once
  `deploy/production-rollout.env` has `CSA_PROD_S3_TRAINING_KEY=prod_s3_training` (Training video,
  below). Until then the training worker mounts the existing `runtime/staging_disabled_model_key`
  file for both names and needs no new file.

Compose refuses to start a service whose secret file is missing. Create them with a hidden prompt
or a pipe, never as a command argument, so a key never lands in shell history or a log:

```bash
sudo install -d -m 0700 -o 1000 -g 1000 "$CSA_STAGING_SECRET_DIR/storage"
read -rsp "Worker access key id: " V; echo
printf %s "$V" | sudo install -m 0400 -o 1000 -g 1000 /dev/stdin "$CSA_STAGING_SECRET_DIR/storage/s3_worker_access_key_id"
read -rsp "Worker secret access key: " V; echo
printf %s "$V" | sudo install -m 0400 -o 1000 -g 1000 /dev/stdin "$CSA_STAGING_SECRET_DIR/storage/s3_worker_secret_access_key"
unset V
```

Do the same for the two `s3_app_*` files. Rotate by writing new files and recreating the services
(`docs/runbooks/05_rotate_credentials.md`).

## Manual shell environment (`~/csa-prod-env.sh`)

The scripted deploy reads `deploy/production-rollout.env` and `/srv/csa-prod/host.env` and
nothing else. For manual work (break-glass, the capacity test) the host keeps one sourced file
with the non-secret values. It stays on the host, never in git (the repository is public).
Besides the staging variables (`CSA_STAGING_PROJECT`, `CSA_STAGING_SECRET_DIR`,
`CSA_STAGING_PYTHON_IMAGE`, `CSA_STAGING_FRONTEND_IMAGE`, `CSA_STAGING_CONSOLE_ORIGIN`,
`CSA_STAGING_HOOKS_ORIGIN`) and the switches it sets:

```bash
export CSA_PROD_S3_BUCKET=<bucket>
export CSA_PROD_S3_REGION=<region>
# Optional: only while the database image package is private and the image was loaded by hand.
export CSA_PROD_DB_IMAGE=csa/pg16-postgis-vector:prod-local

csa_compose() {
  ( cd ~/CSAV2 && docker compose --env-file /dev/null --project-name "$CSA_STAGING_PROJECT" \
    --file deploy/compose.staging-local-proposal.yml \
    --file deploy/compose.staging-shared-caddy.yml \
    --file deploy/compose.staging-pipeline.yml \
    --file deploy/compose.production.yml \
    --profile staging-approved --profile pipeline-approved "$@" )
}
```

This replaces the hand-written override file used during the first rehearsal: the database image
and the gateway's `edge` network now come from `compose.production.yml`, so delete that override
file and drop it from `csa_compose`. The tunnel connector uses the extra
`--profile dedicated-connector-approved`.

## Check before starting anything

```bash
source ~/csa-prod-env.sh
csa_compose --profile dedicated-connector-approved config --quiet && echo "config ok"
```

An error naming `S3 bucket name required` or `S3 region required` means the two `CSA_PROD_S3_*`
lines are missing from the environment file. A missing secret file is named in the error.

## Switching the gateway to S3

Until cutover the worker and delivery stay stopped: the restored data holds live channel
credentials. Start only the services that serve the console.

1. Pre-checks: the secret files above exist and `config ok` printed.
2. Recreate the gateway on the S3-capable image:
   `csa_compose up --detach --wait --wait-timeout 300 --no-deps --pull never gateway`.
   The gateway checks the bucket at startup and stops if the bucket is unreachable or the key is
   refused; read `docker logs <project>-gateway-1` if `--wait` times out.
3. Run the real-bucket check from inside the gateway (prints PASS or FAIL lines, no keys):
   `docker exec <project>-gateway-1 python -m scripts.storage_smoke`.
   Every line must be PASS.
4. Copy the existing upload files into the bucket with the block in `docs/OBJECT_STORAGE.md`
   ("Copy existing local files into S3"), using an admin profile, before any user relies on
   uploads.

## Tuning without a code change

Every number the capacity test may change has the default the tests pin. For scripted deploys,
set an override in `/srv/csa-prod/host.env` (it wins over the repository file for these keys
only); the plan lists the keys under `Host overrides:` and `apply` refuses if they changed since
the plan. The next `deploy-production` recreates gateway, worker, delivery, frontend and the
training worker with the new values. A rollout never recreates the database, embeddings or tunnel
connector, so a change to `CSA_PROD_MEM_DB`, `_EMBEDDINGS`, `_CLOUDFLARED` or a `CSA_PROD_PG_*` value takes effect only
when the owner recreates that service by hand (`csa_compose up --detach --no-deps <service>`). A
migration is never involved.

| Variable | Default | What it sets |
| --- | --- | --- |
| `CSA_PROD_WORKER_REPLICAS` | `2` | Number of workers. Set `1` first if memory is tight. |
| `CSA_PROD_MEM_DB`, `_GATEWAY`, `_WORKER`, `_DELIVERY`, `_FRONTEND`, `_EMBEDDINGS`, `_CLOUDFLARED` | `640m`, `400m`, `320m`, `200m`, `256m`, `1100m`, `96m` | Container memory limit (the worker value is per replica). |
| `CSA_PROD_MEM_TRAINING_WORKER` | `256m` | The training worker's memory limit (one container; `oom_score_adj` 600, the first the OOM killer takes). |
| `CSA_PROD_PG_SHARED_BUFFERS` | `192MB` | Postgres `shared_buffers`. |
| `CSA_PROD_PG_EFFECTIVE_CACHE` | `512MB` | Postgres `effective_cache_size`. |
| `CSA_PROD_PG_WORK_MEM` | `4MB` | Postgres `work_mem`. |
| `CSA_PROD_PG_MAINTENANCE_MEM` | `64MB` | Postgres `maintenance_work_mem`. |
| `CSA_PROD_PG_MAX_CONNECTIONS` | `60` | Postgres `max_connections`. |
| `CSA_PROD_GROQ_CHAT_URL` | Groq | Model endpoint. Capacity test only (below); never accepted in `host.env`. |

## Training video

The training worker (`training-worker`, `python -m apps.training_worker.main`, the same image) runs
on staging and production from this change on. With `CSA_STAGING_TRAINING_VIDEO` unset it opens
nothing and only touches its liveness file; the gateway refuses new video uploads. One process,
always: never give it replicas.

### Before the first production deploy of this change

The training worker starts with that deploy, video on or off. Idle it still holds a Python process
(about 60 MiB resident), and the image is about 378 MB larger on disk (901 MB against 523 MB), with
the previous image kept on the host for a rollback. The compose memory limits already sum to 3332m
against a 2048 MB host, and the training worker adds 256m (3588m). Limits are caps, not use, so
before approving that deploy send, from csa-prod and taken at a busy time:

```bash
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'
free -m
df -h /
```

If the headroom is not there, the choices are a larger host, one worker
(`CSA_PROD_WORKER_REPLICAS=1`) or a lower `CSA_PROD_MEM_TRAINING_WORKER`, decided before the deploy.

### Rolling back past this change

This change adds migrations 0600 and 0601, so its first deploy has migrations pending and never
rolls back by itself (the training worker also had no previous container to go back to). Going
back to a commit from before it is a downgrade across a migration: that commit's `plan` stops with
`production has migrations this checkout lacks: 0600, 0601`, and its images would fail `/readyz`
against 0601 anyway. Fix forward. If the owner decides to go back regardless, roll the two
migrations back first (`docs/RUNBOOK.md`, "Roll back migrations 0600 and 0601"), then deploy the
older commit: with `deploy-production` when it includes the digest change, otherwise as in
"Rolling back to a revision older than the digest change" (break-glass).

Either way the rollout that runs is the older commit's own (the entry script checks out the target
commit and runs its `scripts.staging_rollout`; break-glass uses its compose file), which does not
know the training worker. It finishes without touching `csa-prod-training-worker-1`, which stays on
the newer image (running idle, or stopped if it was stopped for the migration rollback). Remove it
by hand: `docker rm -f csa-prod-training-worker-1`.

Staging is the same: the older checkout's own runner finishes and leaves the container; remove it
with `docker rm -f csa-staging-training-worker-1`.

### Owner steps before video is switched on

1. **Production host capacity.** Take the three readings above again before the switch: a video
   job adds ffmpeg (up to about 54 MiB resident) and the job's buffers to the idle worker. Then the
   thumbnail frame: its worst case (a 4K 10-bit HEVC frame) peaked at 249.5 of 256 MiB beside an
   imports-only process (`docs/TRAINING_VIDEO_CAPACITY.md`, "Thumbnail frame"), not the real worker.
   Run the production image's training worker as production runs it (256 MiB cap, `oom_score_adj`
   600) on a 4K 10-bit HEVC clip, watch `docker events --filter event=oom`, and confirm the item
   reaches `ready`. Staging has no memory cap, so its retest cannot show this. If the worker is
   killed, the requeue skips the frame and the video finishes without a thumbnail; decide on the
   limit before the switch.
2. **The training worker key** (`csa-prod-training`): create it with the policy in
   `docs/OBJECT_STORAGE.md` ("IAM policy for the training worker key"), then write its two files
   with the hidden-prompt block under "Secret files the overlay needs" (names
   `s3_training_access_key_id` and `s3_training_secret_access_key`).
3. **The application key** gains `s3:AbortMultipartUpload` and `s3:ListMultipartUploadParts` on
   `training/*` (the `TrainingMultipart` statement in `docs/OBJECT_STORAGE.md`).
4. **The bucket lifecycle**: incomplete multipart uploads abort after 2 days, not 1
   (`docs/OBJECT_STORAGE.md`, "Bucket settings").
5. **The real-bucket check** in `docs/OBJECT_STORAGE.md` ("Checks only the real bucket can
   answer"): `BadDigest` for a wrong checksum, and `ListParts` returning `ChecksumSHA256`.
6. **The speech endpoint.** The worker sends each video's audio, in 5-minute chunks, to Groq's
   transcription endpoint (`VOICE_STT_ENDPOINT`, set in the compose file) with the model key. Review
   the provider's retention terms for that audio, as `docs/CONSOLE_VOICE.md` already requires for
   console voice, and decide whether they are acceptable for training recordings.
7. **Staging disk.** Staging keeps videos on the `staging-training-uploads` volume (up to 5 GB per
   company, uploads in progress included). Check free space on the staging host (`df -h /`) before
   switching staging on.
8. **The switch** is a reviewed pull request, never a hand edit on a host: `CSA_STAGING_TRAINING_VIDEO=true`
   in `deploy/staging-rollout.env` (staging), and in `deploy/production-rollout.env` both
   `CSA_STAGING_TRAINING_VIDEO=true` and `CSA_PROD_S3_TRAINING_KEY=prod_s3_training` (production,
   after steps 1 to 6). The rollout refuses any other value for either. The next rollout recreates
   the gateway and the training worker with video on; `plan` lists the two key files as missing
   if step 2 was skipped. On production `plan` refuses video on without the key switch
   (`CSA_STAGING_TRAINING_VIDEO=true needs CSA_PROD_S3_TRAINING_KEY=prod_s3_training`) before
   anything changes; and should a training worker ever start with video on and the placeholder
   key, it refuses to start.
9. **After the switch**: `docker exec csa-prod-gateway-1 python -m scripts.storage_smoke` (every
   line PASS, including the multipart and presigned range lines), then the training retest in
   `docs/TRAINING_RETEST.md`.

A future Content-Security-Policy for the console must allow the video source: on S3 the player loads
a presigned link from the bucket's regional host (`media-src https://<bucket>.s3.<region>.amazonaws.com`),
on the local backend the gateway's own origin. No CSP sets `media-src` today.

### Known limits

- **TLS hostname.** The pinned ffmpeg 7.1.1 verifies the certificate chain of the presigned S3 link
  against the image's CA bundle but does not check the host name (module docstring of
  `csa/training/media.py`; a strict-xfail test turns red once a newer build does). Someone able to
  intercept the worker's traffic (DNS or network position) and holding any publicly trusted
  certificate could read that one video's link for its 15 minutes and feed the worker other bytes
  (still decoded inside the hardened child). The owner either accepts this, or funds the follow-up
  in which the worker fetches the bytes from S3 itself (boto3 checks host names) and hands them to
  ffmpeg locally.
- **One gateway process.** The limit on upload pieces in flight is per process. The gateway runs one
  uvicorn process; do not add `--workers` (or a second gateway) without moving that limit into the
  database.
- **Host capacity**: step 1 above. The 256m default is a ceiling chosen before production use was
  seen; tune it with `CSA_PROD_MEM_TRAINING_WORKER` in `/srv/csa-prod/host.env`.
- **Real-bucket behaviour** (step 5) and the https read inside the real runtime image: the image
  built from this change has `/etc/ssl/certs/ca-certificates.crt`, and an https ffprobe there
  completes the TLS handshake with it (`docs/TRAINING_VIDEO_CAPACITY.md`). The first video on
  production is the end-to-end check against the bucket's own host.

## Jev on production (shadow)

`deploy/production-rollout.env` turns Jev on for the staff assistant areas in **shadow** mode
(`docs/RUNBOOK.md`, "Jev for the staff assistant and the customer classifier"): Groq still decides
every email, routing and inbound-injection question, Jev answers beside it, and both answers are
written to `decision_log`. Nothing a user sees changes. The customer classifier stays on the LLM
(`CSA_STAGING_CUSTOMER_DECIDE_MODE_DEFAULT` is not set). The four lines are
`CSA_STAGING_JEV_SECRET=staging_jev_key` and `CSA_STAGING_ASSISTANT_DECIDER_EMAIL`, `_ROUTING` and
`_INBOUND_INJECTION`, each `shadow`. Evidence behind the choice: the evaluation of 2026-11-17 (168
labelled messages, every question met its target) and cascade running on staging since 2026-11-17.

The key file is `/srv/csa-prod/secrets/pipeline/staging_jev_key` (that is `pipeline/staging_jev_key`
under the production secret directory; the production overlay reuses the staging pipeline file's
`staging_jev_key` secret, mounted as `TYPESAFE_API_KEY` on the gateway and the worker). **Until it
exists, `plan` stops with the file listed as missing and nothing is deployed.** Owner steps:

1. **Create a separate production key** with the Jev provider (a new key for production; do not
   reuse the staging key, so each can be rotated or revoked alone).
2. **Write the file** on the instance as the runtime user (UID 1000, not with sudo), from the
   checkout, with the hidden prompt (the key never goes on a command line or into shell history):

   ```bash
   uv run python -m scripts.staging_jev_key --secret-dir /srv/csa-prod/secrets
   ```

   It writes `pipeline/staging_jev_key` with mode `0400` and no trailing newline, refuses to
   overwrite an existing file or follow a symlink, and prints only the file's path. It first runs
   the staging layout check on the directory; if that check refuses the production directory (it
   knows only the staging layout), write the file by hand the same way, refusing to overwrite:

   ```bash
   F=/srv/csa-prod/secrets/pipeline/staging_jev_key
   test ! -e "$F" || { echo "exists; not overwriting"; false; }
   read -rsp "Jev production key: " V; echo
   printf %s "$V" | install -m 0400 /dev/stdin "$F"
   unset V
   ls -l "$F"   # -r-------- 1 <uid 1000 user>, mode 0400
   ```
3. **Deploy** with the `deploy-production` workflow as usual. `plan` lists any file still missing;
   `apply` recreates the gateway and the worker with the key mounted.
4. **Read the log.** Use the three audit queries in the runbook ("Audit queries") on the production
   database: for the staff areas `decision_log` should show `mode = shadow`, `provider` for both
   Jev and the LLM, few `jev_timeout` or `jev_http_*` fallbacks, and few disagreements on confident
   answers. Run them after a few days of real use.
5. **Move to cascade** only by a reviewed PR that changes the three `shadow` lines in
   `deploy/production-rollout.env` to `auto` or `cascade`, after the shadow log looks right, then
   a normal deploy. The customer classifier has its own gate (runbook step 4) and its own PR.
6. **Kill switch.** A reviewed PR that sets the three areas to `llm` (always accepted, needs no
   key), or removes the four lines, then a normal deploy recreates the gateway and the worker.
   To rotate the key: remove the old file, write the new one as in step 2, and restart the gateway
   and the worker (the runbook's "The secret file" has the staging commands and the check).

## Pictures (PR 2b): before the first production deploy

Pull request 2b (the payment QR picture with how-to-pay answers and Telegram reminders) is the first
code that writes picture messages. The code that reads them is pull request 2a, merged as
`c770ad770eb426ebc93ad923e55fd3f588afc05a` (`c770ad77`). Production must run 2a on every process
before any commit containing 2b is deployed there:

1. Check what production runs (the last successful `deploy-production` apply log ends with
   `Rollout of <sha7> finished.`). If that commit already contains `c770ad77`
   (`git merge-base --is-ancestor c770ad77 <running sha>`), go on to the normal deploy.
2. Otherwise deploy `c770ad77`, or a later staged commit on `main` from before 2b, first, with an
   explicit full SHA (`deploy/DEPLOY.md`, "Production: deploying from GitHub", step 2):

   ```bash
   gh workflow run deploy-production --repo amsyrzmi/CSAV2 --ref main -f sha=c770ad770eb426ebc93ad923e55fd3f588afc05a
   ```

   It passes the staged gate (staging ran `c770ad77` on 2026-11-16). Do not start a default run
   (empty `sha`) before this one has finished: an empty `sha` deploys the tip of `main`, which
   contains 2b. Migration 0610 (pictures pull request 1) must be applied on production by then; it
   comes with that commit if it is not there yet, so that first deploy has a migration pending
   and stops the app services while it runs.
3. Only then deploy a commit containing 2b, as usual.

Never roll production back past 2a once 2b has run there: an older delivery process sends a
picture message's caption as plain text, and an older worker fails a saved conversation turn that
carries a picture. Fix forward instead.

## Planner area prompts

The staff assistant can plan each turn with a prompt scoped to the areas it needs (design:
`docs/superpowers/specs/2026-11-20-planner-area-prompts-design.md`). It is behind one optional
switch, wired like the other staging switches:

| Rollout line | Values | Default | Sets |
| --- | --- | --- | --- |
| `CSA_STAGING_WORKSPACE_AREA_PROMPTS` | `false`, `true` | off (unset) | the app setting `WORKSPACE_AREA_PROMPTS` on the gateway |

It is declared in `scripts/rollout_targets.py` (production takes it from the same name) and passed
through `deploy/compose.staging-local-proposal.yml` (default `false`), but it is **not set** in
`deploy/staging-rollout.env` or `deploy/production-rollout.env`, so it is off on both. Switching it on is a separate reviewed pull
request (staging first), after the blockers listed in the design's "Results of the build" are
cleared. Put the line `CSA_STAGING_WORKSPACE_AREA_PROMPTS=true` in a rollout file, never
`WORKSPACE_AREA_PROMPTS`. Setting it back to `false` (or removing the line) restores the full prompt.

## HR default templates

HR PR 4 adds a run-once action that creates the Malaysian default onboarding and offboarding
templates for a company (`POST /console/v1/hr/templates/defaults`, the **Create the default
templates** button in People & HR, Processes, Templates). It needs no migration, so it rolls out
with the images only. It is behind one optional switch:

| Rollout line | Values | Default | Sets |
| --- | --- | --- | --- |
| `CSA_STAGING_HR_DEFAULT_TEMPLATES` | `false`, `true` | off (unset) | the app setting `HR_DEFAULT_TEMPLATES_ENABLED` on the gateway |

It is declared in `scripts/rollout_targets.py` (production takes it from the same name, as it does
`CSA_STAGING_TRAINING_VIDEO`) and passed through `deploy/compose.staging-pipeline.yml`, but it is
**not set** in `deploy/staging-rollout.env` or `deploy/production-rollout.env`, so it is off on both.
Put the line `CSA_STAGING_HR_DEFAULT_TEMPLATES=true` in a rollout file, never
`HR_DEFAULT_TEMPLATES_ENABLED`. While it is off the route answers 404 `resource_unavailable`, the
template list says `defaults.available: false` and the console shows no button.

**Review gate.** The default templates are statutory reminders with references, so the switch stays
off until a named reviewer has read `docs/hr/DEFAULT_TEMPLATES_REVIEW.md` and signed its sign-off
block, and the owner has recorded that sign-off; only then does a reviewed pull request add the
line, to `deploy/staging-rollout.env` first and, after the staging checks in
`docs/hr/PROCESSES_RETEST.md`, to `deploy/production-rollout.env`. Never turn it on by a hand edit on
a host. Turning it on creates nothing by itself: an HR user chooses the button once per company, and
the onboarding default then becomes the company default, so the next person added starts real tasks
and reminders. Setting the line back to `false` (or removing it) hides the button again; templates
already created stay and are edited or deactivated in the console. The full steps are in
`docs/hr/SETUP.md`, "The Malaysian default templates".

## Pointing the model at a stub (capacity test only)

The gateway and worker read the model endpoint from `GROQ_CHAT_URL`. To load-test without spending
model calls, run a stub container on the `provider-egress` network and set
`CSA_PROD_GROQ_CHAT_URL=http://<stub service name>:<port>/v1/chat/completions` in
`~/csa-prod-env.sh`, then recreate the gateway and worker with `csa_compose`. The setting accepts
`https` anywhere, and plain `http` only to a single-label internal name such as a Compose service;
a dotted host or an IP address with `http` stops the process at startup. The model key is sent to
this URL, so unset the variable and recreate both services as soon as the test ends.
