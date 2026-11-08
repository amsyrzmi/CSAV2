"""Offer synthetic Telegram messages to the gateway and report the capacity-gate numbers.

Spec section 6: about 300 messages over 10 minutes. Messages arrive at random moments inside the
window (so there are small bursts), each from its own sender so conversations are independent.
Reports gateway ack latency, then waits for the queue to drain and reports how long that took and
whether any event was lost.

Run it in a container on the ``app`` network (README.md). It reads the owner connection from
``/run/secrets/owner_dsn`` only to count rows; it writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import time
from pathlib import Path

import httpx
import psycopg

GREETINGS = (
    "Hi",
    "Hello, are you open today?",
    "Thanks!",
    "Selamat pagi",
    "Good morning",
    "Terima kasih",
    "Hi there, anyone around?",
    "Ok noted",
)
ACK_P95_LIMIT_MS = 200.0


def percentile(samples: list[float], fraction: float) -> float:
    """Nearest-rank percentile; 0 for no samples."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def update(index: int, text: str, base: int = 0) -> dict[str, object]:
    """A Telegram message update from sender 900000 + index; ``base`` keeps update ids unique."""
    sender = 900_000 + index
    return {
        "update_id": base + index + 1,
        "message": {
            "message_id": base + index + 1,
            "date": int(time.time()),
            "from": {"id": sender, "first_name": "Load"},
            "chat": {"id": sender, "type": "private"},
            "text": text,
        },
    }


async def offer(args: argparse.Namespace) -> tuple[list[float], dict[int, int]]:
    """Send every message at its scheduled moment; return ack times (ms) and status counts."""
    rng = random.Random(args.seed)
    moments = sorted(rng.uniform(0, args.duration) for _ in range(args.count))
    base = int(time.time())  # a rerun must not repeat an earlier run's update ids
    acks: list[float] = []
    statuses: dict[int, int] = {}
    headers = {"X-Telegram-Bot-Api-Secret-Token": args.secret}
    limits = httpx.Limits(max_connections=50)
    async with httpx.AsyncClient(base_url=args.url, timeout=15, limits=limits) as http:
        epoch = time.perf_counter()

        async def send(index: int, moment: float) -> None:
            await asyncio.sleep(max(0.0, epoch + moment - time.perf_counter()))
            body = update(index, rng.choice(GREETINGS), base)
            started = time.perf_counter()
            try:
                response = await http.post(f"/hooks/{args.public_id}", json=body, headers=headers)
                code = response.status_code
            except httpx.HTTPError:
                code = 0
            acks.append((time.perf_counter() - started) * 1000)
            statuses[code] = statuses.get(code, 0) + 1

        await asyncio.gather(*(send(i, m) for i, m in enumerate(moments)))
    return acks, statuses


def database_now(dsn: str) -> object:
    """The database clock; events of this run are the ones created after it."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute("SELECT now()").fetchone()
    return row[0]  # type: ignore[index]


def counts(dsn: str, client_id: str, since: object) -> dict[str, int]:
    """Ingress events of this run (created since ``since``) by status."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT status, count(*) FROM ingress_events "
            "WHERE client_id=%s AND created_at >= %s GROUP BY status",
            (client_id, since),
        ).fetchall()
    return {status: int(n) for status, n in rows}


def outcome(dsn: str, client_id: str, since: object) -> tuple[int, int]:
    """Replies written for this run's events, and deliveries pending for the load tenant."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        replies = conn.execute(
            "SELECT count(*) FROM messages WHERE client_id=%s AND direction='outbound' "
            "AND trace_id IN (SELECT trace_id FROM ingress_events "
            "WHERE client_id=%s AND created_at >= %s)",
            (client_id, client_id, since),
        ).fetchone()
        pending = conn.execute(
            "SELECT count(*) FROM outbound_deliveries WHERE client_id=%s AND status='pending'",
            (client_id,),
        ).fetchone()
    return int(replies[0]), int(pending[0])  # type: ignore[index]


def drain(args: argparse.Namespace, dsn: str, since: object) -> tuple[float, dict[str, int]]:
    """Wait until every offered event is completed (or failed), or the timeout passes."""
    started = time.perf_counter()
    last_print = 0.0
    while True:
        now = counts(dsn, args.client_id, since)
        done = now.get("completed", 0) + now.get("failed", 0) + now.get("dead", 0)
        elapsed = time.perf_counter() - started
        if elapsed - last_print >= 30:
            print(f"  draining: {now} after {elapsed:.0f}s", flush=True)
            last_print = elapsed
        if done >= args.count or elapsed >= args.drain_timeout:
            return elapsed, now
        time.sleep(5)


def main() -> None:
    """Run the offer, the drain and the verdict; print one ``CAPACITY_RESULT`` JSON line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://gateway:8080")
    parser.add_argument("--public-id", required=True)
    parser.add_argument("--secret", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--duration", type=float, default=600.0, help="seconds")
    parser.add_argument("--drain-timeout", type=float, default=3600.0, help="seconds")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    dsn = Path("/run/secrets/owner_dsn").read_text(encoding="utf-8").strip()

    since = database_now(dsn)
    print(f"offering {args.count} messages over {args.duration:.0f}s", flush=True)
    wall = time.perf_counter()
    acks, statuses = asyncio.run(offer(args))
    offered_seconds = time.perf_counter() - wall
    print(f"offered in {offered_seconds:.0f}s; waiting for the queue to drain", flush=True)
    drain_seconds, final = drain(args, dsn, since)
    replies, pending = outcome(dsn, args.client_id, since)

    accepted = statuses.get(200, 0)
    completed = final.get("completed", 0)
    result = {
        "count": args.count,
        "accepted_200": accepted,
        "status_codes": statuses,
        "ack_p50_ms": round(percentile(acks, 0.50), 1),
        "ack_p95_ms": round(percentile(acks, 0.95), 1),
        "ack_p99_ms": round(percentile(acks, 0.99), 1),
        "ack_max_ms": round(max(acks, default=0.0), 1),
        "drain_seconds_after_last_message": round(drain_seconds),
        "events_by_status": final,
        "outbound_replies": replies,
        "deliveries_pending": pending,
        "lost_events": args.count - completed - final.get("failed", 0) - final.get("dead", 0),
    }
    result["ack_ok"] = result["ack_p95_ms"] < ACK_P95_LIMIT_MS and accepted == args.count
    result["drained_ok"] = completed == args.count and replies >= args.count
    print("CAPACITY_RESULT " + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
