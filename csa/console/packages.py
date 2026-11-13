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
            "A shared inbox for your channels, with tickets, handoff to your team and order "
            "approvals. Without an add-on, most customer questions go to your team."
        ),
        kind="base",
        requires=(),
        capabilities=("ticket_agent", "escalation_agent", "order_approval"),
        hr_modules=(),
        email=False,
        tabs=("inbox",),
        sellable=True,
    ),
    Package(
        key="knowledge_base",
        title="Business knowledge",
        summary="Answers customer questions from the files you upload.",
        kind="addon",
        requires=("customer_support",),
        capabilities=("kb_agent",),
        hr_modules=(),
        email=False,
        tabs=("knowledge_base",),
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
        summary=(
            "Keeps your plans and customers' subscriptions, lets customers sign up from the "
            "chat, and lets staff change them or answer requests. With payments on, it sends "
            "invoices and payment reminders, and tells customers how to pay."
        ),
        kind="addon",
        requires=("customer_support",),
        capabilities=("subscription_agent", "payment_agent"),
        hr_modules=(),
        email=False,
        tabs=("subscriptions", "payments"),
        sellable=True,
    ),
    Package(
        key="bookings",
        title="Bookings",
        summary=(
            "A calendar for booking people, rooms and shared things. Your team books in the "
            "console, and customers can book, move or cancel by message."
        ),
        kind="standalone",
        requires=(),
        capabilities=("booking_agent",),
        hr_modules=(),
        email=False,
        tabs=("bookings",),
        sellable=True,
    ),
    Package(
        key="training_library",
        title="Training library",
        summary=(
            "Keeps your company's training slides in one place, so your team can read "
            "them in the browser."
        ),
        kind="standalone",
        requires=(),
        capabilities=(),
        hr_modules=(),
        email=False,
        tabs=("training_library",),
        sellable=True,
    ),
    Package(
        key="marketplaces",
        title="Marketplaces",
        summary=(
            "Connects your TikTok Shop so your assistant can check orders and stock "
            "for customers and your team."
        ),
        kind="addon",
        requires=("customer_support",),
        capabilities=("ecommerce_agent",),
        hr_modules=(),
        email=False,
        tabs=("ecommerce",),
        sellable=True,
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
