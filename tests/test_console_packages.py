from collections import Counter
from pathlib import Path

from csa.console.agents import CATALOG
from csa.console.packages import PACKAGES, backfill_members, package, packages_for_capability


def test_every_customer_agent_belongs_to_exactly_one_package():
    counts = Counter(c for p in PACKAGES for c in p.capabilities)
    assert set(counts) == set(CATALOG) - {"email_assistant"}
    assert all(n == 1 for n in counts.values())


def test_required_safety_capabilities_live_only_in_customer_support():
    assert packages_for_capability("escalation_agent") == (package("customer_support"),)
    assert packages_for_capability("order_approval") == (package("customer_support"),)


def test_addons_require_customer_support_and_standalones_require_nothing():
    for p in PACKAGES:
        assert (p.kind == "addon") == (p.requires == ("customer_support",))
        if p.kind != "addon":
            assert p.requires == ()


def test_sellable_is_false_when_any_member_is_not_implemented():
    for p in PACKAGES:
        if any(CATALOG[c][1] == "not_implemented" for c in p.capabilities):
            assert p.sellable is False
    assert package("marketplaces").sellable is False
    assert package("e_invoicing").sellable is False
    assert package("customer_support").sellable and package("hr").sellable
    assert package("staff_email").sellable


def test_migration_backfill_members_match_catalogue():
    sql = Path("db/migrations/0450_console_service_packages.sql").read_text()
    for members in backfill_members().values():
        for member in members:
            assert f"'{member}'" in sql.split("-- BACKFILL")[1]
