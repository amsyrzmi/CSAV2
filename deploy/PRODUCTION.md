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
- two workers, and memory limits and OOM priorities so the kernel kills a worker or the frontend
  before Postgres;
- Postgres settings for a 2 GB host;
- the gateway joins the `edge` network so the tunnel connector can reach it;
- the database image can be named by the operator (`CSA_PROD_DB_IMAGE`) while the registry package
  is private.

`tests/test_production_overlay.py` renders the stack with synthetic values and checks all of this
against the real settings class. The memory numbers are starting values; the capacity gate decides
whether they hold.

**The rule:** production only receives a commit that staging has already run. A successful
staging rollout records its commit as a successful GitHub Deployment in the environment `staging`;
the production rollout refuses any commit without that record. The one exception is the owner's
break-glass procedure below, which is outside that rule.

## Deploying from GitHub

A deploy is the manually started workflow `deploy-production` (`.github/workflows/deploy-production.yml`).
It has two jobs. `plan` runs in the environment `production-plan`, which has no reviewer on
purpose: it is read-only, and its AWS role can send only the plan document, whose action is fixed
to `plan`. `apply` runs in the environment `production` and waits for one approval from either
reviewer (`amsyrzmi` or `DanSan0408`). Each job gets a short-lived AWS role session through GitHub
OIDC and sends a Systems Manager command to the instance; no port is open and no key is stored.
On the instance the root-owned entry script `/usr/local/sbin/csa-prod-deploy` checks out the
commit in `/home/ubuntu/CSAV2` and runs
`python -m scripts.staging_rollout --target production plan` (or `apply`) as `ubuntu`.

1. **Before you start.** The commit must be on `main`, its images published (the normal
   `publish-staging-images` run), and it must have been rolled out to staging with the
   `staging-rollout` flow, which writes the staged record.
2. **Start it.** GitHub, Actions, `deploy-production`, "Run workflow", branch `main`. Leave `sha`
   empty to deploy the newest staged commit (the newest successful `staging` deployment), or give
   the full 40-character SHA of an older staged commit (a rollback). From a terminal:

   ```bash
   gh workflow run deploy-production --repo amsyrzmi/CSAV2 --ref main
   gh workflow run deploy-production --repo amsyrzmi/CSAV2 --ref main -f sha=<full 40-character sha>
   ```

   Both jobs run only for `main`, and their runner scripts always come from a checkout of `main`,
   never from the commit being deployed.
3. **Read the plan** in the `plan` job's summary. It lists `Target SHA:`, `Staged on:` (the date
   of the staging rollout it found), both image digests, any `WARNING:` lines, the rollback
   references (the images running now), `Pending migrations:`, any missing secret files,
   `Host overrides:` (which `CSA_PROD_*` keys `/srv/csa-prod/host.env` sets; values are never
   printed), `Host settings digest:`, the steps, the downtime line when a migration is pending, and
   the apply command line. The workflow sends exactly the target SHA, migrations and host digest
   from these lines; you never type them. The printed command line (`.venv/bin/python -m
   scripts.staging_rollout --target production apply ...`) is for reference only: the instance
   runs the rollout with `/opt/csa-deploy/venv/bin/python` through `csa-prod-deploy`.
4. **Approve or reject** the `production` deployment on the run page ("Review deployments"). One
   approval from either reviewer is enough; self-approval is allowed. Approval is the decision,
   so read the plan first: a migration stops gateway, frontend, both workers and delivery (console
   unavailable, bot replies delayed, not lost), and a downgrade warning means the target is older
   than what runs now. Reject to cancel; nothing on production has changed (only
   the deploy checkout moved to the commit).
5. **Read the result** in the `apply` job's summary. `apply` checks out `main` again at approval
   time for its runner scripts, then sends the planned values; on the instance the rollout
   re-derives the plan and refuses if anything changed. It checks the compose config, takes and
   verifies a backup (`Backup taken: backups/csa_staging-<timestamp>.dump`), pulls and verifies
   the images by digest, applies the pending migrations (services stopped), recreates gateway,
   worker (both replicas), delivery and frontend, waits for `/readyz`, runs the check files, and
   ends with `Rollout of <sha7> finished.`. On a stop it prints `Stopped at <step>: ...`, the state
   production is in, the recovery lines for how far it got, and
   `Re-run plan, then apply, once the cause is fixed.` It never retries, restarts or rolls back by
   itself. Every run is also logged on the instance in
   `/var/log/csa-prod-deploy/<timestamp>-<action>-<sha7>.log`. Systems Manager's
   `GetCommandInvocation` returns at most 24,000 characters of output, so a long apply loses its
   tail (the recovery lines) from the workflow log and summary: the complete log is always the
   one on the instance in `/var/log/csa-prod-deploy/`.

### What a failure means

The runner (`deploy/production/ssm_run.sh`) exits with:

- **0**: the command succeeded on the instance.
- **124**: the command was still `Pending`, `InProgress` or `Delayed` when the runner stopped
  waiting, 115 minutes (by the clock, not a count of polls) after it sent the command. The command
  may still be running or may still start. **Check the
  host; do not start the workflow again.** On the instance: `ls -t /var/log/csa-prod-deploy | head -n 3`,
  read the newest log, and `pgrep -af csa-prod-deploy`. In AWS: Systems Manager, Run Command,
  Command history, the command id from the line `ssm_run: sent <action> for <sha> as command <id>`.
  Systems Manager itself stops a command that runs longer than 120 minutes (the documents'
  `timeoutSeconds`, longer than the runner waits), so a command still running at 124 may yet
  finish or be stopped.
- **64**: the runner refused an argument or setting; nothing was sent.
- **1**: anything else, including a command that failed on the instance. The line
  `ssm_run: command <id> finished with status <status> (exit code <N>)` gives the instance's exit
  code: **75** means another deploy holds the lock (`another deploy is running`); wait for it to
  finish and start again. **64** means `csa-prod-deploy` refused its arguments. **73** means it
could not write its log in `/var/log/csa-prod-deploy` (`cannot write the log file`); nothing ran,
and the directory must be owned by `ubuntu`. **2** is a plan
  that found missing secret files. **1** is a rollout stop, `commit is not on main`, or a failed
  `git fetch`. A `TimedOut` status also arrives as runner exit 1, not 124: either the instance did
  not pick the command up within 10 minutes (it never ran), or Systems Manager stopped it at its
  40-minute limit (an apply may have stopped part-way). Check the host as for 124 before any new
  run; the instance's log shows which.

A `Configure AWS credentials` step that fails with `Not authorized to perform
sts:AssumeRoleWithWebIdentity` means the role's trust policy does not match the token the job
presented: the subject (see One-time setup, step 3, for how to look up the prefix GitHub issues for
this repository) or the environment name. Nothing was sent to the instance.
Fix it with `aws iam update-assume-role-policy --role-name <role> --policy-document file://<policy>`
and start the workflow again.

A `Require the deploy variables` step that fails names the secret or variable that is not set
(see One-time setup, step 6). With an empty `sha`, `no successful staging deployment found` means
no commit has a staged record yet. A failed `Read the plan` step (`parse_plan: ...`) means the
plan output lacked one of its `Target SHA:`, `Pending migrations:` or `Host settings digest:`
lines, or named another commit; `apply` does not run.

### What each refusal means

- `Stopped at gate: commit <sha7> has not been rolled out to staging`: staging never recorded this
  commit. Roll it out to staging first. There is no bypass. If the staging rollout succeeded but
  printed `WARNING: staging is fine but the staged record was not written`, run its `Retry:` line
  on the home server: `.venv/bin/python -m scripts.staging_rollout record-staged --sha <full sha>`.
  It refuses a commit that is not on `origin/main` in that checkout.
- `images for <sha7> are not published yet (<state>)`: approve the `staging-images` environment
  on the publish run, wait for it to finish, then start again.
- `Stopped at confirm: host settings changed since plan; run plan again`: `/srv/csa-prod/host.env`
  changed between plan and approval. Start the workflow again.
- `HEAD changed since plan` or `pending migrations changed since plan`: production or `main`
  moved between plan and approval. Start the workflow again.
- `WARNING: <sha7> is older than the running <sha7>; this is a downgrade. A downgrade across a
  migration is refused.`: a warning, not a stop. Approve only if you mean to go back.
  `WARNING: could not compare <sha7> with the running revision` is expected on the first deploy,
  when the running images are local tags.
- `<target> has migrations this checkout lacks: ...; update the checkout before rolling out`
  (`production has migrations ...`): the target commit is older than the database schema, a
  downgrade across a migration. It is refused; fix forward instead.
- Host file stops (`Stopped at flags: ...`): `cannot read host.env`, `host.env is missing: <keys>`,
  `host.env line <n>: unknown key <KEY>`, `duplicate key`,
  `quoted value; write KEY=value without quotes`, or
  `set in both the repo file and the host file: <keys>` (a switch belongs in
  `deploy/production-rollout.env` only). Fix the file on the instance and start again.
- Backup stops (`Stopped at backup: ...`, always followed by
  `Nothing on production was changed. The backup step failed; the deploy stopped before any change.`):
  - `/srv/csa-prod/backup/backup.sh is missing; reinstall it: install -m 0755 deploy/production/backup.sh /srv/csa-prod/backup/backup.sh`
    or `... differs from deploy/production/backup.sh; reinstall it: ...`: the commit changed the
    backup script. As `ubuntu`, run `cd ~/CSAV2 && install -m 0755 deploy/production/backup.sh /srv/csa-prod/backup/backup.sh`
    (the plan left the checkout at the target commit), then start the workflow again.
  - `the backup did not report ok: <timestamp> skipped: previous backup still running`: the
    6-hourly cron backup held the lock. Wait a minute and start again.
  - `/srv/csa-prod/backup/backup.sh failed (exit <n>); run it on the instance to see why`, or a
    log line that is old or has no timestamp: run `/srv/csa-prod/backup/backup.sh` as `ubuntu`
    (no `sudo`) and read its error. The deploy does not print the script's output, because an AWS
    error can carry the bucket name.
- `missing secret files: ...`: create the files (Secret files below), then start again.
- After a stop with migrations applied, the recovery text names the backup taken at the start
  (`The backup taken at the start of this deploy is backups/csa_staging-<timestamp>.dump`); fix
  forward, or restore it (Restoring a backup).
- Image-only stop after the recreate: `To roll back: git checkout <sha> in /home/ubuntu/CSAV2, run
  plan and apply, then git checkout main.` On production that means: start `deploy-production`
  with `sha` set to that revision (it is a staged commit); do not touch the checkout. On the first
  deploy the previous images are local tags: the text says `follow the manual rollback runbook`,
  which is "Rolling back to the local images" under Break-glass.

## One-time setup

The owner does this once, after the deploy PR is merged to `main`. Each step names where it runs.
`<region>` is the one AWS region used for production; the activation, the two documents, the two
role policies and `PROD_DEPLOY_REGION` must all use it. Nothing in this section is ever pasted
into the repository or a chat; secret values go in at a hidden prompt.

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

The trust policies name the token's subject, and GitHub decides its shape per repository: the classic
`repo:<owner>/<repo>` or, for repositories that use the immutable subject, `repo:<owner>@<owner id>/<repo>@<repo id>`.
Ask GitHub which one this repository issues, on a machine where `gh` is logged in as a repository
admin, for example your own machine (the endpoint needs the admin login):

```bash
gh api repos/amsyrzmi/CSAV2/actions/oidc/customization/sub --jq .sub_claim_prefix
```

Copy the printed value into CloudShell as `SUBJECT=<printed value>`, in the same tab as the commands
below. A wrong value is the usual cause of `Not authorized to perform sts:AssumeRoleWithWebIdentity`
at the workflow's AWS step (see What a failure means).

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

`csa-production-plan` comes from `deploy/production/ssm-document-plan.json` (one parameter, `sha`)
and `csa-production-apply` from `deploy/production/ssm-document-apply.json` (`sha`, `migrations`,
`hostDigest`). Every parameter has an `allowedPattern`, so Systems Manager rejects anything else,
and each document runs `runuser -u ubuntu -- /usr/local/sbin/csa-prod-deploy` with its action
written in. When a PR changes a document file, update it the same way:
`aws ssm update-document --name <name> --content file://<file> --document-version '$LATEST'`,
then `aws ssm update-document-default-version --name <name> --document-version <new version>`.

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
   Systems Manager runs), and its log directory. Reinstall the script the same way whenever a PR
   changes `deploy/production/deploy_entry.sh`.

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
   `deploy/production-rollout.env` by a reviewed PR too; none is set today. `grep -n "[\"']" /srv/csa-prod/host.env` must print nothing.

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

g. GitHub read access for `gh`, which looks up the image digests (GitHub's package API needs a
   token even for public packages) and reads the `staging` deployments. The token comes from the
   other developer: a classic token with only the `read:packages` scope. `gh auth login
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
   the public images by digest with an empty Docker config, so it would not use one.

h. Run a plan on the instance exactly as Systems Manager will, for the newest staged commit:

   ```bash
   SHA=$(gh api "repos/amsyrzmi/CSAV2/deployments?environment=staging&per_page=1" --jq '.[0].sha')
   sudo runuser -u ubuntu -- /usr/local/sbin/csa-prod-deploy plan "$SHA" "" ""
   ```

   It must end with the apply command line and no `Stopped at` line. Whenever you test the entry
   script by hand, run it as `sudo runuser -u ubuntu -- /usr/local/sbin/csa-prod-deploy ...`,
   never as root directly: a root run leaves the lock file root-owned, and every later run then
   fails at the lock with `Permission denied`.

### 6. GitHub (repository Settings, as `amsyrzmi`): two environments, three secrets, one variable

- Environment **`production-plan`**: no required reviewer. Deployment branches and tags:
  "Selected branches and tags", one rule for the branch `main`. Environment secrets:
  `PROD_PLAN_ROLE_ARN` (the `csa-prod-deploy-plan` role ARN) and `PROD_INSTANCE_ID`
  (`<managed-instance-id>`).
- Environment **`production`**: Required reviewers `amsyrzmi` and `DanSan0408` (one approval is
  enough; `DanSan0408` needs at least read access to the repository, which they have). Leave
  "Prevent self-review" off. Deployment branches and tags: only `main`, as above. Environment
  secrets: `PROD_APPLY_ROLE_ARN` (the `csa-prod-deploy-apply` role ARN) and `PROD_INSTANCE_ID`.
- Repository variable **`PROD_DEPLOY_REGION`**: `<region>`.

The `main`-only restriction on both environments is mandatory: the roles' trust policies name the
environment, and the environment's branch rule is what keeps another branch from getting that
token (the jobs also check `github.ref`). The role ARNs and instance id are secrets so the public
workflow logs mask them.

Create the two environments, their reviewers and branch rules first, in the web settings
(Settings, Environments); `gh secret set --env` for an environment that does not exist yet fails
with HTTP 404. Then set the secrets and the variable, in the web settings or from a terminal, each
secret typed at `gh`'s hidden prompt:

```bash
gh secret set PROD_PLAN_ROLE_ARN --repo amsyrzmi/CSAV2 --env production-plan
gh secret set PROD_INSTANCE_ID --repo amsyrzmi/CSAV2 --env production-plan
gh secret set PROD_APPLY_ROLE_ARN --repo amsyrzmi/CSAV2 --env production
gh secret set PROD_INSTANCE_ID --repo amsyrzmi/CSAV2 --env production
gh variable set PROD_DEPLOY_REGION --repo amsyrzmi/CSAV2 --body <region>
```

### 7. GitHub: verify with a plan

Start `deploy-production` with an empty `sha` (Deploying from GitHub, step 2) and read the plan.
On the first run it must show `Staged on:`, both images by digest, the rollback references as
`unknown (a local image with no registry digest, or not running)`, and the pending migrations.
Optionally check, on the instance, that the image pulls without credentials (the plan's
`python:` digest; the pull is reused by the apply):

```bash
D=$(mktemp -d); DOCKER_CONFIG="$D" docker pull ghcr.io/amsyrzmi/csa-python@<digest from the plan>; rm -rf "$D"
```

Then reject the `production` deployment, or approve it to make the first deploy, watched live.

### First production deploy: what to check

- The first plan's log (`/var/log/csa-prod-deploy/`) shows no `getcwd` or permission lines before
  `Target SHA:`, and `gh` finds its login (no `gh auth login` prompt or HTTP 401).
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

1. Stop the services that write: `docker stop csa-prod-gateway-1 csa-prod-frontend-1 csa-prod-worker-1 csa-prod-worker-2 csa-prod-delivery-1`.
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
   `docker start csa-prod-gateway-1 csa-prod-frontend-1 csa-prod-worker-1 csa-prod-worker-2 csa-prod-delivery-1`.
   If the dump is older, do not start them: start `deploy-production` with `sha` set to the commit
   production runs, which applies the missing migrations and recreates the services.
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

4. Only if the commit has migrations the database lacks: stop the four services, then migrate.

   ```bash
   csa_compose stop gateway frontend worker delivery
   CSA_STAGING_MIGRATION_APPROVAL=approved-isolated-staging csa_compose --profile migration-approved run --rm --no-deps migrate
   ```

5. Recreate the services one by one, in this order, and read each result:

   ```bash
   for S in gateway worker delivery frontend; do
     csa_compose up --detach --wait --wait-timeout 300 --no-deps --pull never "$S" || break
   done
   ```

6. Check readiness with the probe in "Restoring a backup", step 5.

A commit deployed this way has no staged record, so a later `deploy-production` of the same commit
refuses until staging has run it.

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

Then check readiness as above. Do not delete the local tags until a scripted deploy has succeeded.
If migrations were applied, do not roll back; fix forward.

## Keys and who holds what

| Service | S3 key | Allowed |
| --- | --- | --- |
| gateway | application key (`csa-prod-app`) | `kb/`, `hr/` and `training/` read, write, delete; `backups/` put-only |
| worker (two replicas) | worker key (`csa-prod-worker`) | `kb/` read only |
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
the plan. The next `deploy-production` recreates gateway, worker, delivery and frontend with the
new values. A rollout never recreates the database, embeddings or tunnel connector, so a change
to `CSA_PROD_MEM_DB`, `_EMBEDDINGS`, `_CLOUDFLARED` or a `CSA_PROD_PG_*` value takes effect only
when the owner recreates that service by hand (`csa_compose up --detach --no-deps <service>`). A
migration is never involved.

| Variable | Default | What it sets |
| --- | --- | --- |
| `CSA_PROD_WORKER_REPLICAS` | `2` | Number of workers. Set `1` first if memory is tight. |
| `CSA_PROD_MEM_DB`, `_GATEWAY`, `_WORKER`, `_DELIVERY`, `_FRONTEND`, `_EMBEDDINGS`, `_CLOUDFLARED` | `640m`, `400m`, `320m`, `200m`, `256m`, `1100m`, `96m` | Container memory limit (the worker value is per replica). |
| `CSA_PROD_PG_SHARED_BUFFERS` | `192MB` | Postgres `shared_buffers`. |
| `CSA_PROD_PG_EFFECTIVE_CACHE` | `512MB` | Postgres `effective_cache_size`. |
| `CSA_PROD_PG_WORK_MEM` | `4MB` | Postgres `work_mem`. |
| `CSA_PROD_PG_MAINTENANCE_MEM` | `64MB` | Postgres `maintenance_work_mem`. |
| `CSA_PROD_PG_MAX_CONNECTIONS` | `60` | Postgres `max_connections`. |
| `CSA_PROD_GROQ_CHAT_URL` | Groq | Model endpoint. Capacity test only (below); never accepted in `host.env`. |

## Pointing the model at a stub (capacity test only)

The gateway and worker read the model endpoint from `GROQ_CHAT_URL`. To load-test without spending
model calls, run a stub container on the `provider-egress` network and set
`CSA_PROD_GROQ_CHAT_URL=http://<stub service name>:<port>/v1/chat/completions` in
`~/csa-prod-env.sh`, then recreate the gateway and worker with `csa_compose`. The setting accepts
`https` anywhere, and plain `http` only to a single-label internal name such as a Compose service;
a dotted host or an IP address with `http` stops the process at startup. The model key is sent to
this URL, so unset the variable and recreate both services as soon as the test ends.
