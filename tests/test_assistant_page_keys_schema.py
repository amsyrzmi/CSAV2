"""Schema contract for migration 0611: the assistant's page-key check also allows the Payments and
Connections tabs, keeps every earlier key, and a second run changes nothing."""

from __future__ import annotations

import typing

from csa.workspace.contracts import TabKey
from scripts import migrate

from tests.conftest import requires_docker
from tests.test_bookings_bot_schema import PAGE_CHECK, _accepts, _allowed, _definitions
from tests.test_bookings_schema import _owner
from tests.test_training_schema import _page_turn

pytestmark = requires_docker


def test_page_key_check_allows_payments_and_connections_and_keeps_earlier_keys(
    migrated_dsn: str,
) -> None:
    with _owner(migrated_dsn) as conn:
        allowed = _allowed(conn, PAGE_CHECK)
    earlier = {
        "workspace_home",
        "business_orders",
        "business_order",
        "overview",
        "services",
        "inbox",
        "knowledge_base",
        "business",
        "hr",
        "email",
        "activity",
        "chat",
        "subscriptions",
        "bookings",
        "training_library",
    }
    assert earlier <= allowed
    assert {"payments", "connections"} <= allowed
    assert set(typing.get_args(TabKey)) <= allowed
    assert _accepts(migrated_dsn, _page_turn("payments"))
    assert _accepts(migrated_dsn, _page_turn("connections"))
    assert not _accepts(migrated_dsn, _page_turn("not_a_page"))


def test_running_0611_again_changes_nothing(migrated_dsn: str) -> None:
    before = _definitions(migrated_dsn)
    [path] = [p for v, p in migrate.discover_migrations() if v == "0611"]
    with _owner(migrated_dsn) as conn:
        conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
    assert _definitions(migrated_dsn) == before
