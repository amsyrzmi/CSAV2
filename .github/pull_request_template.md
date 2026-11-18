## What and why

<!-- One or two sentences. Link the plan or issue. -->

## Checks

- [ ] Tests for the changed behaviour (the changed files' tests pass locally; CI runs the full suite)
- [ ] `make lint` passes
- [ ] Needs host preparation before merge (new secret file, switch or host step): <what>

<!-- If the box above applies, say what the owner must do and in which order. A merge to `main`
rolls out to staging by itself (deploy-staging), so anything the new code needs on the host
(a secret file, a new secret directory, a switch) must exist before the merge, or the run turns
red. To hold rollouts while you prepare: gh variable set STAGING_AUTO_PAUSED --body true. -->

- [ ] No hostname, address, account id, node id, ARN, bucket or real secret in the diff (the repository is public)
