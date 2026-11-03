"""The staging roster: who the staging reset creates, validated before anything is touched.

The roster holds personal addresses, so it lives in an ignored local file and no error text
here ever repeats an address: problems are reported by their position in the file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from csa.console.contracts import EmailInput
from csa.console.onboarding import ClientIn
from csa.console.packages import PACKAGES
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
ROSTER_PATH = REPO_ROOT / "deploy" / "staging-accounts.local.json"

_PACKAGES = {package.key: package for package in PACKAGES}


class RosterError(ValueError):
    """The roster is unreadable or breaks a rule. The text never includes an address."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _address(value: object) -> str:
    try:
        return EmailInput.model_validate({"email": value}).email
    except ValidationError:
        raise ValueError("not a valid email address") from None


class PlatformAdmin(_Frozen):
    email: str
    display_name: str = Field(min_length=1, max_length=120)

    _normalised = field_validator("email", mode="before")(_address)


class Member(_Frozen):
    email: str
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal["tenant_admin", "tenant_staff", "read_only"]

    _normalised = field_validator("email", mode="before")(_address)


class Company(_Frozen):
    slug: str
    name: str
    timezone: str
    packages: tuple[str, ...]
    deployment_mode: Literal["shadow", "suggest", "auto"] | None = None
    members: tuple[Member, ...]

    @model_validator(mode="after")
    def _company_rules(self) -> Company:
        # Slug, name and timezone follow the rules the console applies to a new company.
        try:
            ClientIn(slug=self.slug, name=self.name, timezone=self.timezone)
        except ValidationError as exc:
            fields = ", ".join(sorted({str(err["loc"][0]) for err in exc.errors()}))
            raise ValueError(f"invalid company {fields}") from None
        if len(set(self.packages)) != len(self.packages):
            raise ValueError("a package is listed twice")
        for key in self.packages:
            package = _PACKAGES.get(key)
            if package is None:
                raise ValueError(f"unknown package {key!r}")
            if not package.sellable:
                raise ValueError(f"package {key!r} is not sold")
            missing = [need for need in package.requires if need not in self.packages]
            if missing:
                raise ValueError(f"package {key!r} requires {', '.join(missing)}")
        return self

    def ordered_packages(self) -> tuple[str, ...]:
        """The listed packages, each after the ones it requires."""
        ordered: list[str] = []

        def visit(key: str) -> None:
            if key in ordered:
                return
            for need in _PACKAGES[key].requires:
                visit(need)
            ordered.append(key)

        for key in self.packages:
            visit(key)
        return tuple(ordered)


class Roster(_Frozen):
    platform_admins: tuple[PlatformAdmin, ...]
    companies: tuple[Company, ...]

    @model_validator(mode="after")
    def _roster_rules(self) -> Roster:
        if not self.platform_admins:
            raise ValueError("at least one platform admin is needed")
        slugs: dict[str, int] = {}
        for index, company in enumerate(self.companies):
            if company.slug in slugs:
                raise ValueError(
                    f"companies[{index}] repeats the slug of companies[{slugs[company.slug]}]"
                )
            slugs[company.slug] = index
            if not any(member.role == "tenant_admin" for member in company.members):
                raise ValueError(f"companies[{index}] needs at least one tenant_admin")
        seen: dict[str, str] = {}
        places = [
            (admin.email, f"platform_admins[{i}]") for i, admin in enumerate(self.platform_admins)
        ] + [
            (member.email, f"companies[{c}].members[{m}]")
            for c, company in enumerate(self.companies)
            for m, member in enumerate(company.members)
        ]
        for email, place in places:
            if email in seen:
                raise ValueError(f"{place} repeats the address of {seen[email]}")
            seen[email] = place
        return self


def _describe(exc: ValidationError) -> str:
    """One line per problem, by position. Never uses the offending input."""
    lines = []
    for err in exc.errors():
        place = ""
        for part in err["loc"]:
            place += f"[{part}]" if isinstance(part, int) else f".{part}" if place else str(part)
        message = err["msg"].removeprefix("Value error, ")
        lines.append(f"{place}: {message}" if place else message)
    return "; ".join(lines)


def parse_roster(data: object) -> Roster:
    try:
        return Roster.model_validate(data)
    except ValidationError as exc:
        raise RosterError(f"roster is not valid: {_describe(exc)}") from None


def load_roster(path: Path) -> Roster:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RosterError(f"roster file not found: {path}") from None
    except OSError as exc:
        raise RosterError(f"roster file cannot be read: {exc.strerror}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RosterError(
            f"roster is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from None
    return parse_roster(data)
