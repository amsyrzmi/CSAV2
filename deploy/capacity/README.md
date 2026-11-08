# Capacity gate runbook (spec section 6)

Run on the production instance, by hand, from a checkout of this branch. Nothing here is part of
the application image, so a fix to any file here needs no image rebuild.

What it does: offers about 300 synthetic Telegram messages over 10 minutes to the real gateway,
with the model replaced by a stub that waits about as long as the real one. It then measures how
fast the gateway acknowledges, how long the queue takes to drain, and what memory, swap, CPU
steal and OOM kills the instance shows. A short real-model run (about 50 turns) follows.

**Pass:** gateway ack p95 under 200 ms; every event completed with a reply, none lost; no OOM
kill; memory headroom (the sampler's lowest `available` stays above about 150 MB and swap does
not keep growing); CPU burst credits not exhausted (steal stays low, and the Lightsail
`BurstCapacityPercentage` metric stays above zero).

**What this measures and what it does not:** the stub makes every message a greeting, so the
turn is classify, plan, step, compose and review with no tool calls. That exercises ingress, the
queue, the durable graph, Postgres and the worker processes, which is where RAM and CPU go. It
does not exercise order, booking or knowledge-base lookups.

**Safety:** the delivery service stays stopped, so nothing is sent to a channel. The load tenant
is synthetic and has its own slug (`capacity-gate-...`). The production database used here is
the rehearsal copy; it is replaced by a fresh dump at cutover, which discards the load rows.
Do not keep this database through cutover.

## Steps

1. Fetch these files onto the instance (read-only checkout of a pushed branch):

   ```bash
   git -C ~/CSAV2 fetch origin chore/capacity-gate
   git -C ~/CSAV2 checkout --detach FETCH_HEAD
   chmod +x ~/CSAV2/deploy/capacity/sample.sh
   ls ~/CSAV2/deploy/capacity
   ```

2. Check that no real customer event is waiting. The worker would answer it with the stub. The
   query prints status counts; `received` and `dispatched` must be absent or zero:

   ```bash
   docker exec csa-prod-staging-db-1 psql -U csa -d csa_staging -tA \
     -c "SELECT status, count(*) FROM ingress_events GROUP BY status"
   ```

3. Create the synthetic tenant (prints its ids as JSON and saves them):

   ```bash
   docker run --rm --user 1000:1000 --network csa-prod_data -e PYTHONPATH=/app \
     -v /srv/csa-prod/secrets/migration/staging_owner_dsn:/run/secrets/owner_dsn:ro \
     -v /srv/csa-prod/secrets/runtime/staging_credentials_key:/run/secrets/CREDENTIALS_KEY:ro \
     -v ~/CSAV2/deploy/capacity:/capacity:ro \
     --entrypoint python csa/python:prod-c561cac /capacity/seed_load_tenant.py | tee ~/capacity-tenant.json
   ```

4. Start the model stub on the egress network, under the name `model-stub`:

   ```bash
   docker run -d --name csa-model-stub --network csa-prod_provider-egress \
     --network-alias model-stub --user 1000:1000 --memory 64m -e PYTHONUNBUFFERED=1 \
     -v ~/CSAV2/deploy/capacity:/capacity:ro \
     --entrypoint python csa/python:prod-c561cac /capacity/model_stub.py 8080
   docker exec csa-model-stub python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080').read())"
   ```

   The last line prints `b'{}'`.

5. Start the two workers against the stub (delivery stays stopped):

   ```bash
   source ~/csa-prod-env.sh
   ( export CSA_PROD_GROQ_CHAT_URL=http://model-stub:8080/v1/chat/completions
     csa_compose up --detach --wait --wait-timeout 300 --no-deps --pull never worker )
   csa_compose ps worker
   ```

   A worker that exits at once has a bucket, key or database problem; read
   `docker logs csa-prod-worker-1 --tail 40`.

6. Check what the worker key may do with a missing file. `StorageNotFound` is the expected
   answer (ingest retries treat it as "gone"); anything else means the key policy needs a look:

   ```bash
   docker exec csa-prod-worker-1 python -c "
   import uuid
   from csa.core.settings import get_settings
   from csa.storage.factory import build_stores
   kb = build_stores(get_settings()).kb
   try:
       kb.read(uuid.uuid4(), 'missing.txt', 10)
   except Exception as error:
       print('worker key, missing object:', type(error).__name__)
   "
   ```

7. Smoke run, five messages. All five must complete and the stub must have counted calls:

   ```bash
   T=~/capacity-tenant.json
   docker run --rm --network csa-prod_data --user 1000:1000 \
     -v /srv/csa-prod/secrets/migration/staging_owner_dsn:/run/secrets/owner_dsn:ro \
     -v ~/CSAV2/deploy/capacity:/capacity:ro \
     --entrypoint python csa/python:prod-c561cac /capacity/load_driver.py \
     --public-id "$(python3 -c "import json;print(json.load(open('$T'))['public_id'])")" \
     --secret "$(python3 -c "import json;print(json.load(open('$T'))['secret'])")" \
     --client-id "$(python3 -c "import json;print(json.load(open('$T'))['client_id'])")" \
     --count 5 --duration 10 --drain-timeout 300
   docker exec csa-model-stub python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080').read())"
   ```

   Expect `"drained_ok": true`, and stub counts of five each for classify, plan, step, compose
   and review (a step or review count of zero is fine if the graph skipped that call).

8. Start the sampler, then the full run. The run takes about 10 minutes of offering plus the
   drain (expected 12 to 25 minutes in all). It runs in the background, so an SSH drop is safe:

   ```bash
   nohup nice -n 19 ~/CSAV2/deploy/capacity/sample.sh /var/tmp/capacity-samples.log 15 >/dev/null 2>&1 &
   T=~/capacity-tenant.json
   docker run -d --name csa-load-driver --network csa-prod_data --user 1000:1000 \
     -v /srv/csa-prod/secrets/migration/staging_owner_dsn:/run/secrets/owner_dsn:ro \
     -v ~/CSAV2/deploy/capacity:/capacity:ro \
     --entrypoint python csa/python:prod-c561cac /capacity/load_driver.py \
     --public-id "$(python3 -c "import json;print(json.load(open('$T'))['public_id'])")" \
     --secret "$(python3 -c "import json;print(json.load(open('$T'))['secret'])")" \
     --client-id "$(python3 -c "import json;print(json.load(open('$T'))['client_id'])")" \
     --count 300 --duration 600
   docker logs -f csa-load-driver
   ```

   `Ctrl+C` leaves the run going. The last line is `CAPACITY_RESULT {...}`.

9. Read the instance numbers from the sampler:

   ```bash
   L=/var/tmp/capacity-samples.log
   echo "lowest available MB: $(grep -o 'available=[0-9]*' $L | cut -d= -f2 | sort -n | head -1)"
   echo "highest swap used MB: $(grep -o 'swap_used=[0-9]*' $L | cut -d= -f2 | sort -n | tail -1)"
   echo "highest CPU steal %: $(grep -o 'steal=[0-9]*' $L | cut -d= -f2 | sort -n | tail -1)"
   echo "OOM kills: $(grep -c 'oom_killed=true' $L)"
   grep -E 'restarts=[1-9]' $L | sort -u
   ```

   CPU burst credits live in AWS, not on the host. On the machine with the AWS login (replace the
   profile and the two times, UTC, around the run):

   ```bash
   aws lightsail get-instance-metric-data --region ap-southeast-1 --profile PROFILE \
     --instance-name csa-prod --metric-name BurstCapacityPercentage --period 300 --unit Percent \
     --statistics Minimum --start-time 2026-11-08T12:00:00Z --end-time 2026-11-08T14:00:00Z \
     --query 'metricData[].[timestamp,minimum]' --output table
   ```

10. Real-model run (about 50 turns, owner's model key). Only after step 9 is fine. Send the
    workers back to the real model, then run the driver with `--count 50 --duration 300` (same
    command as step 8):

    ```bash
    source ~/csa-prod-env.sh
    csa_compose up --detach --wait --wait-timeout 300 --no-deps --pull never worker
    docker rm -f csa-load-driver
    ```

11. Clean up when done. The workers stay stopped until cutover:

    ```bash
    source ~/csa-prod-env.sh
    docker rm -f csa-load-driver csa-model-stub
    csa_compose stop worker
    pkill -f deploy/capacity/sample.sh
    ```

If any step fails, stop and report the output before going on. If the gate fails, the order of
remedies is in the spec: tune swap, Postgres and worker count first (`deploy/PRODUCTION.md`,
"Tuning without a code change"); a lighter embedding model or a larger plan needs the owner.
