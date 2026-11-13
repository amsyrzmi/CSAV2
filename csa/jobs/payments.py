"""The payments_due job: opening and period invoices, overdue marking, optional unpaid suspend."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import structlog

from csa.core import db
from csa.jobs.clock import Clock
from csa.jobs.subscriptions import PACKAGE_KEYS, PACKAGE_STATUSES
from csa.payments.invoices import InvoiceRecord, issue_opening_invoice, issue_period_invoice
from csa.payments.settings import get_settings

logger = structlog.get_logger(__name__)

Conn = psycopg.AsyncConnection[Any]
Issuer = Callable[..., Awaitable[InvoiceRecord | None]]

NOTHING_DUE = {"opening_issued": 0, "period_issued": 0, "overdue": 0, "suspended": 0, "failed": 0}


async def run_payments_due(
    conn: Conn, client_id: UUID, clock: Clock, *, batch_size: int = 100
) -> dict[str, int]:
    """Issue the invoices that fall due for one company, mark late ones overdue, maybe suspend.

    Nothing happens (zero counts) unless the company's subscriptions package is active (as for
    the subscriptions_due job) and its payments are switched on (``payment_settings.enabled``).
    Then, in the runner's tenant transaction:

    1. up to ``batch_size`` pending subscriptions without an opening invoice get one
       (``issue_opening_invoice``, due ``lead_days`` from now);
    2. up to ``batch_size`` active subscriptions, not cancelling at period end, whose next period
       starts after now and within ``lead_days`` and has no invoice yet get one
       (``issue_period_invoice``, due when the period starts). A period that already started is
       never invoiced, so switching payments on bills no running period;
    3. up to ``batch_size`` issued invoices past their due time become overdue;
    4. only when ``suspend_after_days`` is set: up to ``batch_size`` active subscriptions with an
       overdue invoice due more than that many days ago are paused as unpaid (no resume date;
       paying lifts it), with one ``suspended_unpaid`` event each. An invoice suspends at most
       once: one that already caused a ``suspended_unpaid`` event no longer counts, so a resume
       while it stays overdue is not undone by the next run.

    Each issue runs in its own savepoint: one that raises is undone, counted in ``failed`` and
    logged as ``payments_issue_failed`` with the error class and ids only, and the run goes on.
    Rows are claimed ``FOR UPDATE SKIP LOCKED`` and the selections leave out rows that would get
    no invoice, so a run never waits on another and a batch is never used up by rows that issue
    nothing. Running twice, or two runs at once, issues one invoice per period: the second finds
    the invoice or skips the locked row, and ``invoices_subscription_period_uq`` stays the last
    guard. Nothing is charged and nobody is messaged here.
    """
    package = await db.q.fetch_one(
        "console/service_package_any_in_status.sql",
        (client_id, PACKAGE_KEYS, PACKAGE_STATUSES),
        conn=conn,
    )
    if package is None:
        return dict(NOTHING_DUE)
    settings = await get_settings(conn, client_id)
    if not settings.enabled:
        return dict(NOTHING_DUE)

    now = clock.now()
    params: dict[str, Any] = {"client_id": client_id, "now": now, "batch_size": batch_size}
    stats = dict(NOTHING_DUE)

    pending = await db.q.fetch_all("jobs/payments_due_opening.sql", params, conn=conn)
    for row in pending:
        await _issue(conn, client_id, row["id"], now, "opening", stats)

    horizon = now + timedelta(days=settings.lead_days)
    renewing = await db.q.fetch_all(
        "jobs/payments_due_period.sql", {**params, "horizon": horizon}, conn=conn
    )
    for row in renewing:
        await _issue(conn, client_id, row["id"], now, "period", stats)

    marked = await _one("jobs/payments_due_overdue.sql", params, conn)
    stats["overdue"] = int(marked["overdue"])

    if settings.suspend_after_days is not None:
        cutoff = now - timedelta(days=settings.suspend_after_days)
        claimed = await db.q.fetch_all(
            "jobs/payments_due_suspend_lock.sql", {**params, "cutoff": cutoff}, conn=conn
        )
        if claimed:
            suspended = await _one(
                "jobs/payments_due_suspend.sql",
                {**params, "cutoff": cutoff, "ids": [row["id"] for row in claimed]},
                conn,
            )
            stats["suspended"] = int(suspended["suspended"])
    return stats


async def _issue(
    conn: Conn,
    client_id: UUID,
    subscription_id: UUID,
    now: datetime,
    kind: str,
    stats: dict[str, int],
) -> None:
    """Issue one opening or period invoice in a savepoint; a failure is counted and logged."""
    issuer: Issuer = issue_opening_invoice if kind == "opening" else issue_period_invoice
    try:
        async with conn.transaction():
            issued = await issuer(conn, client_id, subscription_id, now=now)
    except Exception as exc:  # one bad row must not stop the others; the savepoint undid it
        stats["failed"] += 1
        # The class only: an error message can carry amounts, names or addresses.
        logger.error(
            "payments_issue_failed",
            client_id=str(client_id),
            subscription_id=str(subscription_id),
            kind=kind,
            error_class=type(exc).__name__,
        )
        return
    if issued is not None:
        stats[f"{kind}_issued"] += 1


async def _one(path: str, params: dict[str, Any], conn: Conn) -> Any:
    row = await db.q.fetch_one(path, params, conn=conn)
    if row is None:
        raise RuntimeError(f"{path} returned no row")
    return row
