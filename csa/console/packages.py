"""Service package catalogue: sellable groupings of customer agents, HR modules and email."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Kind = Literal["base", "addon", "standalone"]


class Package(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    title: str
    summary: str
    kind: Kind
    requires: tuple[str, ...]
    capabilities: tuple[str, ...]
    hr_modules: tuple[str, ...]
    email: bool
    tabs: tuple[str, ...]
    sellable: bool


_HR_MODULES = (
    "hr_directory",
    "hr_training",
    "hr_recruitment",
    "hr_attendance",
    "hr_payroll",
    "hr_assistant",
)

PACKAGES: tuple[Package, ...] = (
    Package(
        key="customer_support",
        title="Customer support",
        summary=(
            "Answers customer questions from your knowledge base and handles tickets, "
            "escalations and order approvals."
        ),
        kind="base",
        requires=(),
        capabilities=("kb_agent", "ticket_agent", "escalation_agent", "order_approval"),
        hr_modules=(),
        email=False,
        tabs=("inbox", "knowledge_base"),
        sellable=True,
    ),
    Package(
        key="orders_products",
        title="Orders and products",
        summary="Looks up products, drafts orders and shows invoice status for your team.",
        kind="addon",
        requires=("customer_support",),
        capabilities=("order_agent", "invoice_agent"),
        hr_modules=(),
        email=False,
        tabs=("business",),
        sellable=True,
    ),
    Package(
        key="subscriptions",
        title="Subscriptions",
        summary="Shows subscription plans and status and records unexecuted subscription requests.",
        kind="addon",
        requires=("customer_support",),
        capabilities=("subscription_agent",),
        hr_modules=(),
        email=False,
        tabs=("subscriptions",),
        sellable=True,
    ),
    Package(
        key="bookings",
        title="Bookings",
        summary="Keeps booking history and records booking requests for staff to action.",
        kind="addon",
        requires=("customer_support",),
        capabilities=("booking_agent",),
        hr_modules=(),
        email=False,
        tabs=("bookings",),
        sellable=True,
    ),
    Package(
        key="marketplaces",
        title="Marketplaces",
        summary=("Connects your e-commerce store; marketplace setup is needed before use."),
        kind="addon",
        requires=("customer_support",),
        capabilities=("ecommerce_agent",),
        hr_modules=(),
        email=False,
        tabs=("ecommerce",),
        sellable=False,
    ),
    Package(
        key="e_invoicing",
        title="E-invoicing (LHDN MyInvois)",
        summary="Prepares LHDN MyInvois e-invoicing; submission is not yet available.",
        kind="addon",
        requires=("customer_support",),
        capabilities=("lhdn_agent",),
        hr_modules=(),
        email=False,
        tabs=("lhdn_myinvois",),
        sellable=False,
    ),
    Package(
        key="hr",
        title="HR",
        summary=(
            "Runs staff directory, training, recruitment, attendance, leave, payroll "
            "and HR assistance."
        ),
        kind="standalone",
        requires=(),
        capabilities=(),
        hr_modules=_HR_MODULES,
        email=False,
        tabs=("hr",),
        sellable=True,
    ),
    Package(
        key="staff_email",
        title="Staff email",
        summary=("Gives staff Gmail triage, summaries and reply drafts saved as drafts only."),
        kind="standalone",
        requires=(),
        capabilities=(),
        hr_modules=(),
        email=True,
        tabs=("email",),
        sellable=True,
    ),
)

BY_KEY: dict[str, Package] = {p.key: p for p in PACKAGES}


def package(key: str) -> Package:
    return BY_KEY[key]


def packages_for_capability(name: str) -> tuple[Package, ...]:
    return tuple(p for p in PACKAGES if name in p.capabilities)


def backfill_members() -> dict[str, tuple[str, ...]]:
    return {p.key: p.capabilities for p in PACKAGES}
