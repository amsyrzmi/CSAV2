"""Who may change a company's training library.

Reading is open to every human member of the company (the router's tenant and package checks).
Managing is for a platform admin acting on the company, its tenant admins, and its HR owner when
that person is an enabled staff member. The HR owner is asked of ``training_is_hr_owner()`` (0541),
which reads ``app.client_id`` and ``app.hr_user_id`` from the transaction. ``app.hr_user_id`` is set
here from the authenticated session's user, with the helper ``hr_owner.py`` uses, and from nothing a
request carries. Nothing is cached: every request asks again.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from csa.console.contracts import ConsoleActor, Role
from csa.core import db


async def can_manage(conn: AsyncConnection[Any], actor: ConsoleActor, client_id: UUID) -> bool:
    """Whether ``actor`` may add, edit and remove items of ``client_id``.

    ``conn`` must be inside ``tx(pool, client_id)``, which sets ``app.client_id``; this sets
    ``app.hr_user_id`` for the rest of that transaction.
    """
    if actor.platform_admin:
        return True  # The tenant dependency has checked the company exists.
    if actor.client_id != client_id:
        return False
    if actor.role == Role.TENANT_ADMIN:
        return True
    if actor.role != Role.TENANT_STAFF:
        return False  # read_only, or no role at all
    await db.q.execute("console/invite_facts_context.sql", (str(actor.user_id),), conn=conn)
    row = await db.q.fetch_one("training/is_hr_owner.sql", conn=conn)
    return row is not None and row["owner"] is True
