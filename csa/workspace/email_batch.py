"""Batch email: who a plan's email goes to (recipients, groups and the send quota).

``expand_recipients`` turns the planner's ``RecipientSpec`` list into people with an address, by
the same checks the one-recipient compose path applies: the HR package for directory people, the
Subscriptions package and its read rule for customers and groups, and ``check_address`` for typed
addresses. Everyone it cannot write to is counted by reason, never named, so a card or a turn row
holds counts only. ``BatchRecipient.label`` is for the assistant's own reply and is never stored.

Paging contract. ``limit`` (1..20, the hourly send ceiling) is an OVERALL cap on ``recipients``:
named people, typed addresses and customers come first in spec order, the one group fills what is
left, and everyone beyond the cap is counted ``over_limit``. ``offset`` pages the GROUP only and
means "skip this many group members already offered"; when ``offset > 0`` the non-group specs are
not expanded again (they were on page one), so they add nothing and are not counted as duplicates.
At most one group spec is allowed per request.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from psycopg import AsyncConnection

from csa.console.contracts import ConsoleActor
from csa.console.services import require_package
from csa.core import db
from csa.core.tenancy import tx
from csa.email.models import check_address
from csa.subscriptions.api import require_subscriptions_read
from csa.subscriptions.service import search_customers
from csa.workspace.contracts import RecipientSpec
from csa.workspace.directory import search_people

NOT_INCLUDED_REASONS = ("no_email", "not_found", "ambiguous", "duplicate", "over_limit")
MAX_LIMIT = 20  # the hourly send ceiling (EmailService.remaining_sends)
_CANDIDATES = 2  # two matches already make a name ambiguous; never fetch more than needed


@dataclass(frozen=True)
class BatchRecipient:
    """One person to write to. ``key`` is stable and opaque (an id or a digest, never the
    address); ``label`` is the name or address for the assistant's reply only."""

    key: str
    employee_id: UUID | None
    address: str
    customer_id: UUID | None
    label: str


@dataclass
class Expanded:
    """The people found, and a count per reason for each one left out."""

    recipients: list[BatchRecipient] = field(default_factory=list)
    not_included: dict[str, int] = field(default_factory=dict)


class _Collector:
    """Adds recipients in order up to ``limit``, counting duplicates (addresses compare
    case-insensitively) and anyone past the cap as ``over_limit``."""

    def __init__(self, limit: int) -> None:
        self.out = Expanded()
        self.limit = limit
        self._seen: set[str] = set()

    def count(self, reason: str, how_many: int = 1) -> None:
        if how_many > 0:
            self.out.not_included[reason] = self.out.not_included.get(reason, 0) + how_many

    def add(self, recipient: BatchRecipient) -> None:
        folded = recipient.address.strip().lower()
        if folded in self._seen:
            self.count("duplicate")
            return
        if len(self.out.recipients) >= self.limit:
            self.count("over_limit")
            return
        self._seen.add(folded)
        self.out.recipients.append(recipient)


def _typed_key(address: str) -> str:
    return "address:" + hashlib.sha256(address.strip().lower().encode()).hexdigest()[:16]


def _address_of(value: str | None) -> str | None:
    """The address if the stored value holds one, else None (empty or blank means no email)."""
    text = (value or "").strip()
    return text or None


def _typed(spec: RecipientSpec) -> str:
    try:
        return check_address(spec.value.strip())
    except ValueError:
        # The compose path refuses a bad typed address the same way.
        raise HTTPException(422, "email_recipient_invalid") from None


async def _employee(
    conn: AsyncConnection[Any], client_id: UUID, spec: RecipientSpec, found: _Collector
) -> None:
    people = await search_people(conn, client_id, spec.value)
    if not people:
        found.count("not_found")
    elif len(people) > 1:
        found.count("ambiguous")  # never pick one of several
    elif (address := _address_of(people[0].work_email)) is None:
        found.count("no_email")
    else:
        person = people[0]
        found.add(
            BatchRecipient(
                key=f"employee:{person.employee_id}",
                employee_id=person.employee_id,
                address=address,
                customer_id=None,
                label=person.display_name or address,
            )
        )


async def _customer(
    conn: AsyncConnection[Any], client_id: UUID, spec: RecipientSpec, found: _Collector
) -> None:
    customers = await search_customers(conn, client_id, q=spec.value, limit=_CANDIDATES)
    if not customers:
        found.count("not_found")
    elif len(customers) > 1:
        found.count("ambiguous")
    elif (address := _address_of(customers[0].email)) is None:
        found.count("no_email")
    else:
        customer = customers[0]
        found.add(
            BatchRecipient(
                key=f"customer:{customer.id}",
                employee_id=None,
                address=address,
                customer_id=customer.id,
                label=customer.full_name or address,
            )
        )


async def _group(
    conn: AsyncConnection[Any],
    client_id: UUID,
    spec: RecipientSpec,
    found: _Collector,
    *,
    offset: int,
) -> None:
    room = found.limit - len(found.out.recipients)
    # LIMIT 0 would return no row, hiding the group and its total: look at one row instead and
    # add only what fits.
    rows = await db.q.fetch_all(
        "subscriptions/customers_by_group.sql",
        {
            "client_id": client_id,
            "group": spec.group,
            "days": spec.days or 0,
            "limit": max(room, 1),
            "offset": offset,
        },
        conn=conn,
    )
    if not rows:
        return
    total, page = rows[0]["total"], rows[:room]
    for row in page:
        address = row["email"].strip()
        found.add(
            BatchRecipient(
                key=f"customer:{row['id']}",
                employee_id=None,
                address=address,
                customer_id=row["id"],
                label=row["full_name"] or address,
            )
        )
    found.count("over_limit", total - offset - len(page))


async def expand_recipients(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    specs: list[RecipientSpec],
    *,
    offset: int,
    limit: int,
) -> Expanded:
    """The people behind ``specs``, in spec order, each address once, at most ``limit`` of them.

    Named people, typed addresses and customers come first; the one group fills what is left, and
    everyone beyond ``limit`` is counted ``over_limit`` (for a group, the members the page fetch
    saw past the cap). ``offset`` pages the group only: when it is above zero the non-group specs
    are skipped, not expanded again and not counted as duplicates, because page one returned them.
    A group is paged in the database by customer id. Every refusal is raised before any row is
    read: ``limit`` outside 1..20 or a negative ``offset`` (422 ``email_batch_limit_invalid``),
    more than one group spec (422 ``email_batch_one_group``), an inactive package, a role that may
    not read Subscriptions, a malformed typed address.
    """
    if not 1 <= limit <= MAX_LIMIT or offset < 0:
        raise HTTPException(422, "email_batch_limit_invalid")
    if sum(spec.kind == "group" for spec in specs) > 1:
        raise HTTPException(422, "email_batch_one_group")
    if offset > 0:
        specs = [spec for spec in specs if spec.kind == "group"]  # the rest were on page one
    pool = request.app.state.pool
    typed = {id(spec): _typed(spec) for spec in specs if spec.kind == "address"}
    if any(spec.kind in ("customer", "group") for spec in specs):
        require_subscriptions_read(actor, client_id)
        await require_package(pool, client_id, frozenset({"subscriptions"}))
    if any(spec.kind == "employee" for spec in specs):
        # Directory people need the hr package, as POST /email/compose does.
        await require_package(pool, client_id, frozenset({"hr"}))
    found = _Collector(limit)
    async with tx(pool, str(client_id)) as conn:
        for spec in sorted(specs, key=lambda spec: spec.kind == "group"):  # the group fills last
            if spec.kind == "address":
                address = typed[id(spec)]
                found.add(
                    BatchRecipient(
                        key=_typed_key(address),
                        employee_id=None,
                        address=address,
                        customer_id=None,
                        label=address,
                    )
                )
            elif spec.kind == "employee":
                await _employee(conn, client_id, spec, found)
            elif spec.kind == "customer":
                await _customer(conn, client_id, spec, found)
            else:
                await _group(conn, client_id, spec, found, offset=offset)
    return found.out
