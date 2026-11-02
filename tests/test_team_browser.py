"""Real browser acceptance for the friendlier Team page.

The real Next.js shell runs unmodified from the disposable staged copy that
`test_services_redesign_browser.site` builds. Its layouts fetch `/auth/me` and `/services/active`
server-side, so the loopback stub from `test_services_browser` answers those; every browser-side
`/console/v1/*` call is answered by Playwright from `World`, which keeps a small company
(members, invitations), records every change the page sends, and can hold or refuse a change so
the tests can prove what asks first, what acts once and what the page says afterwards.
`test_invite_access_browser.py` pins the invite form itself; this file pins the layout around it.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import Page, expect

from tests import test_services_redesign_browser as services
from tests.test_services_browser import TENANT, TENANT_PROFILE, WAIT, make_me
from tests.test_services_redesign_browser import Site, with_browser

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

THEMES = ("cobalt", "night", "bureau")
WIDTHS = (320, 390, 768, 1024, 1440)
AIMAN = "44444444-4444-4444-8444-000000000001"
SITI = "44444444-4444-4444-8444-000000000002"
OMAR = "44444444-4444-4444-8444-000000000003"
SIGNED_OUT = "will be signed out of this company straight away"


def make_member(
    user_id: str, name: str, email: str, role: str, enabled: bool = True
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "client_id": TENANT,
        "email": email,
        "display_name": name,
        "role": role,
        "is_enabled": enabled,
    }


def make_invite(
    invite_id: str,
    email: str,
    name: str,
    *,
    kind: str = "invite",
    expires: str = "2099-01-01T00:00:00Z",
    consumed: bool = False,
    revoked: bool = False,
) -> dict[str, Any]:
    return {
        "id": invite_id,
        "email": email,
        "display_name": name,
        "role": "tenant_staff" if kind == "invite" else None,
        "kind": kind,
        "expires_at": expires,
        "consumed_at": "2026-11-01T01:00:00Z" if consumed else None,
        "revoked_at": "2026-11-01T02:00:00Z" if revoked else None,
        "hr_preset": None,
    }


def default_members() -> list[dict[str, Any]]:
    return [
        make_member(AIMAN, "Aiman Yusof", "aiman@example.test", "tenant_admin"),
        make_member(SITI, "Siti Aminah", "siti@example.test", "tenant_staff"),
        make_member(OMAR, "Omar Lee", "omar@example.test", "read_only", enabled=False),
    ]


def default_invites() -> list[dict[str, Any]]:
    return [
        make_invite("77777777-7777-4777-8777-000000000001", "lina@example.test", "Lina Wong"),
        make_invite(
            "77777777-7777-4777-8777-000000000002",
            "farah@example.test",
            "Farah Ibrahim",
            expires="2026-10-01T00:00:00Z",
        ),
        make_invite(
            "77777777-7777-4777-8777-000000000003",
            "zack@example.test",
            "Zack Tan",
            revoked=True,
        ),
        make_invite(
            "77777777-7777-4777-8777-000000000004",
            "hana@example.test",
            "Hana Lim",
            consumed=True,
        ),
        make_invite(
            "77777777-7777-4777-8777-000000000005",
            "siti@example.test",
            "Siti Aminah",
            kind="reset",
        ),
    ]


@dataclass
class World:
    """Browser-side answers. Every change the page sends is recorded in `calls`."""

    members: list[dict[str, Any]] = field(default_factory=default_members)
    invites: list[dict[str, Any]] = field(default_factory=default_invites)
    calls: list[dict[str, Any]] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    # A change waits on this event (when set) before it is answered: a double click acts once.
    hold: asyncio.Event | None = None
    # Answers the next member change or session revoke with this status and detail.
    refuse: tuple[int, str] | None = None

    def sent(self, method: str, prefix: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method and c["path"].startswith(prefix)]

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        parts = urlsplit("http://x/" + request.url.split("/console/v1/")[1])
        path, params = parts.path.lstrip("/"), parse_qs(parts.query)
        method = request.method
        body: dict[str, Any] = request.post_data_json if request.post_data else {}
        status, data = 200, None
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "profile" and method == "GET":
            data = TENANT_PROFILE
        elif path == "admin/clients" and method == "GET":
            data = [TENANT_PROFILE]
        elif path == "services/active" and method == "GET":
            data = {"packages": []}
        elif path == "team" and method == "GET" and params.get("client_id") == [TENANT]:
            data = self.members
        elif path == "admin/invites" and method == "GET" and params.get("client_id") == [TENANT]:
            data = self.invites
        elif path.startswith("team/") and method == "PATCH" and params.get("client_id") == [TENANT]:
            self.calls.append({"method": method, "path": path, "body": body})
            if self.hold is not None:
                await self.hold.wait()
            if self.refuse:
                status, detail = self.refuse
                self.refuse = None
                await request_route.fulfill(status=status, json={"detail": detail})
                return
            user = path.split("/")[1]
            for member in self.members:
                if member["user_id"] == user:
                    member.update(body)
            status = 204
        elif path == "team/invites" and method == "POST":
            self.calls.append({"method": method, "path": path, "body": body})
            if self.refuse:
                status, detail = self.refuse
                self.refuse = None
                await request_route.fulfill(status=status, json={"detail": detail})
                return
            self.invites.insert(
                0,
                make_invite(
                    "77777777-7777-4777-8777-0000000000a1", body["email"], body["display_name"]
                ),
            )
            data = {
                "id": "77777777-7777-4777-8777-0000000000a1",
                "token": None,
                "expires_at": "2026-11-09T02:00:00Z",
                "delivery": "email",
                "delivery_note": None,
            }
        elif re.fullmatch(r"admin/users/[\w-]+/revoke-sessions", path) and method == "POST":
            self.calls.append({"method": method, "path": path, "body": body})
            if self.refuse:
                status, detail = self.refuse
                self.refuse = None
                await request_route.fulfill(status=status, json={"detail": detail})
                return
            status = 204
        elif re.fullmatch(r"admin/users/[\w-]+/reset", path) and method == "POST":
            self.calls.append({"method": method, "path": path, "body": body})
            data = {
                "id": "77777777-7777-4777-8777-0000000000b1",
                "token": "synthetic-reset-token",
                "expires_at": "2026-11-09T02:00:00Z",
                "delivery": "manual",
                "delivery_note": None,
            }
        elif re.fullmatch(r"admin/invites/[\w-]+/renew", path) and method == "POST":
            self.calls.append({"method": method, "path": path, "body": body})
            data = {
                "id": path.split("/")[2],
                "token": None,
                "expires_at": "2026-11-09T02:00:00Z",
                "delivery": "email",
                "delivery_note": None,
            }
        elif re.fullmatch(r"admin/invites/[\w-]+", path) and method == "DELETE":
            self.calls.append({"method": method, "path": path, "body": None})
            status = 204
        else:
            self.unexpected.append(f"{method} {path}")
            status, data = 500, {"detail": "unexpected synthetic call"}
        try:
            if data is None:
                await request_route.fulfill(status=status)
            else:
                await request_route.fulfill(status=status, json=data)
        except Exception:
            pass


@asynccontextmanager
async def team_page(
    browser: Any,
    site: Site,
    world: World,
    *,
    width: int = 1280,
    height: int = 1000,
    theme: str | None = None,
    platform: bool = False,
    wait: str | None = "team-members",
) -> AsyncIterator[Page]:
    site.upstream.me = make_me("platform_admin" if platform else "tenant_admin")
    site.upstream.packages = [{"key": "customer_support", "status": "active"}]
    context = await browser.new_context(
        viewport={"width": width, "height": height}, timezone_id="Asia/Kuala_Lumpur"
    )
    context.set_default_navigation_timeout(WAIT)
    context.set_default_timeout(15_000)
    cookies = [{"name": "csa_session", "value": "synthetic-session", "url": site.base}]
    if theme:
        cookies.append({"name": "fs-theme", "value": theme, "url": site.base})
    await context.add_cookies(cookies)
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/console/v1/**", world.route)
    await page.goto(f"{site.base}{'/settings/team' if platform else '/portal/team'}")
    if platform:
        await expect(page.locator("#selected-tenant")).to_be_visible(timeout=WAIT)
        await expect(page.locator("#selected-tenant option")).to_have_count(2)
        await page.locator("#selected-tenant").select_option(TENANT)
    if wait:
        await expect(page.get_by_test_id(wait)).to_be_visible(timeout=WAIT)
    try:
        yield page
        assert not errors, errors
        assert not world.unexpected, world.unexpected
        assert not site.upstream.unexpected, site.upstream.unexpected
    finally:
        await context.close()


def card(page: Page, user_id: str) -> Any:
    return page.get_by_test_id(f"team-card-{user_id}")


async def open_card(page: Page, user_id: str) -> Any:
    await page.get_by_test_id(f"team-card-{user_id}-toggle").click()
    details = page.get_by_test_id(f"team-card-{user_id}-details")
    await expect(details).to_be_visible()
    return details


async def focused(page: Page) -> str | None:
    return await page.evaluate("() => document.activeElement?.dataset?.testid ?? null")


async def overflow(page: Page) -> dict[str, int]:
    return await page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
    )


async def test_member_cards_show_role_and_status(site: Site) -> None:
    async def body(browser: Any) -> None:
        async with team_page(browser, site, World()) as page:
            await expect(page.get_by_role("heading", name="Your team")).to_be_visible()
            await expect(
                page.get_by_text("Invite people and manage their access to the selected tenant.")
            ).to_be_visible()
            await expect(page.get_by_role("heading", name="Members", exact=True)).to_be_visible()
            await expect(page.get_by_test_id("team-members").get_by_role("listitem")).to_have_count(
                3
            )
            expected = {
                AIMAN: ("AY", "Aiman Yusof", "aiman@example.test", "Tenant admin", "Active"),
                SITI: ("SA", "Siti Aminah", "siti@example.test", "Tenant staff", "Active"),
                OMAR: ("OL", "Omar Lee", "omar@example.test", "Read only", "Disabled"),
            }
            for user_id, (initials, name, email, role, status) in expected.items():
                one = card(page, user_id)
                await expect(page.get_by_test_id(f"team-card-{user_id}-avatar")).to_have_text(
                    initials
                )
                await expect(one).to_contain_text(name)
                await expect(one).to_contain_text(email)
                await expect(page.get_by_test_id(f"team-card-{user_id}-role")).to_have_text(role)
                await expect(page.get_by_test_id(f"team-card-{user_id}-status")).to_have_text(
                    status
                )
                # Access controls and the original wording sit behind the arrow, closed.
                await expect(one).to_have_attribute("data-open", "false")
                await expect(page.get_by_label(f"Role for {email}")).to_be_hidden()
            await expect(page.get_by_text("Membership enabled")).to_have_count(2)
            await expect(page.get_by_text("Membership enabled").first).to_be_hidden()
            await expect(page.get_by_text("Membership disabled")).to_have_count(1)
            await expect(page.get_by_role("button", name="Disable access").first).to_be_hidden()

            # Opening the arrow shows the controls with their original labels and wording.
            details = await open_card(page, SITI)
            await expect(details).to_contain_text("Membership enabled")
            await expect(page.get_by_label("Role for siti@example.test")).to_have_value(
                "tenant_staff"
            )
            await expect(details.get_by_role("button", name="Disable access")).to_be_visible()
            # A tenant administrator does not see the platform-only tools.
            await expect(details.get_by_role("button", name="Issue reset")).to_have_count(0)
            await expect(details.get_by_role("button", name="Revoke sessions")).to_have_count(0)
            details = await open_card(page, OMAR)
            await expect(details).to_contain_text("Membership disabled")
            await expect(details.get_by_role("button", name="Enable access")).to_be_visible()

            # Invitations: a compact list, one plain chip each, the original line behind the arrow.
            chips = {
                "77777777-7777-4777-8777-000000000001": "Waiting",
                "77777777-7777-4777-8777-000000000002": "Expired",
                "77777777-7777-4777-8777-000000000003": "Cancelled",
                "77777777-7777-4777-8777-000000000004": "Joined",
                "77777777-7777-4777-8777-000000000005": "Waiting",
            }
            await expect(
                page.get_by_role("heading", name="Invitations and reset links")
            ).to_be_visible()
            for invite_id, chip in chips.items():
                await expect(page.get_by_test_id(f"team-invitation-{invite_id}-chip")).to_have_text(
                    chip
                )
            lina = "77777777-7777-4777-8777-000000000001"
            await expect(page.get_by_test_id(f"team-invitation-{lina}")).to_have_attribute(
                "data-open", "false"
            )
            await expect(page.get_by_test_id(f"team-invitation-{lina}-details")).to_be_hidden()
            await page.get_by_test_id(f"team-invitation-{lina}-toggle").click()
            lina_details = page.get_by_test_id(f"team-invitation-{lina}-details")
            await expect(lina_details).to_contain_text("Invitation · Pending")
            await expect(
                lina_details.get_by_role("button", name="Renew invitation")
            ).to_be_visible()
            await expect(lina_details.get_by_role("button", name="Revoke link")).to_be_visible()
            # Expired: renew and revoke. Cancelled: renew only. Joined: neither.
            farah = "77777777-7777-4777-8777-000000000002"
            zack = "77777777-7777-4777-8777-000000000003"
            hana = "77777777-7777-4777-8777-000000000004"
            reset = "77777777-7777-4777-8777-000000000005"
            for invite_id in (farah, zack, hana, reset):
                await page.get_by_test_id(f"team-invitation-{invite_id}-toggle").click()
            details = lambda invite_id: page.get_by_test_id(f"team-invitation-{invite_id}-details")  # noqa: E731
            await expect(details(farah)).to_contain_text("Invitation · Expired")
            await expect(details(zack)).to_contain_text("Invitation · Revoked")
            await expect(details(zack).get_by_role("button", name="Revoke link")).to_have_count(0)
            await expect(details(hana)).to_contain_text("Invitation · Accepted")
            await expect(details(hana).get_by_role("button")).to_have_count(0)
            await expect(details(reset)).to_contain_text("Password reset · Pending")
            await expect(
                details(reset).get_by_role("button", name="Renew invitation")
            ).to_have_count(0)

    await with_browser(body)


async def test_role_change_asks_first_and_cancel_changes_nothing(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with team_page(browser, site, world) as page:
            await open_card(page, SITI)
            select = page.get_by_label("Role for siti@example.test")
            confirm = page.get_by_test_id(f"team-confirm-{SITI}")
            await expect(confirm).to_have_count(0)

            # Choosing a role only asks: nothing is sent, the person is named, the sign-out is said.
            # select_option does not focus the select, so a person's hand on it is stood in for.
            await select.focus()
            await select.select_option("read_only")
            await expect(confirm).to_be_visible()
            await expect(confirm).to_contain_text("Siti Aminah")
            await expect(confirm).to_contain_text("Read only")
            await expect(confirm).to_contain_text(
                f"Siti Aminah {SIGNED_OUT} and will need to sign in again."
            )
            await expect(select).to_have_value("read_only")
            assert world.sent("PATCH", "team/") == []
            # Focus stays on the select, so the arrow keys can keep choosing a role.
            assert await focused(page) == f"team-role-{SITI}"

            # Cancel puts the old role back, sends nothing and returns to the select.
            await page.get_by_test_id(f"team-confirm-{SITI}-cancel").click()
            await expect(confirm).to_have_count(0)
            await expect(select).to_have_value("tenant_staff")
            assert world.sent("PATCH", "team/") == []
            assert await focused(page) == f"team-role-{SITI}"

            # Escape cancels too.
            await select.focus()
            await select.select_option("tenant_admin")
            await expect(confirm).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(confirm).to_have_count(0)
            await expect(select).to_have_value("tenant_staff")
            assert world.sent("PATCH", "team/") == []
            assert await focused(page) == f"team-role-{SITI}"

            # Closing the arrow with a question open cancels it as well.
            await select.select_option("read_only")
            await expect(confirm).to_be_visible()
            await page.get_by_test_id(f"team-card-{SITI}-toggle").click()
            await expect(card(page, SITI)).to_have_attribute("data-open", "false")
            await expect(confirm).to_have_count(0)
            await open_card(page, SITI)
            await expect(select).to_have_value("tenant_staff")
            assert world.sent("PATCH", "team/") == []

            # Choosing the same role again asks nothing.
            await select.select_option("tenant_staff")
            await expect(confirm).to_have_count(0)

            # Confirming sends the one change, keeps focus on the select and updates the pill.
            await select.focus()
            await select.select_option("read_only")
            await page.get_by_test_id(f"team-confirm-{SITI}-yes").click()
            await expect(confirm).to_have_count(0)
            await expect(page.get_by_test_id(f"team-card-{SITI}-role")).to_have_text("Read only")
            sent = world.sent("PATCH", "team/")
            assert [(c["path"], c["body"]) for c in sent] == [
                (f"team/{SITI}", {"role": "read_only"})
            ]
            assert await focused(page) == f"team-role-{SITI}"

            # A refusal (the last administrator) is shown beside the question; nothing changes.
            await open_card(page, AIMAN)
            world.refuse = (409, "cannot remove the last tenant administrator")
            await page.get_by_label("Role for aiman@example.test").select_option("tenant_staff")
            await page.get_by_test_id(f"team-confirm-{AIMAN}-yes").click()
            error = page.get_by_test_id(f"team-confirm-{AIMAN}-error")
            await expect(error).to_have_text("cannot remove the last tenant administrator")
            await expect(
                page.get_by_text("cannot remove the last tenant administrator")
            ).to_have_count(1)
            await page.get_by_test_id(f"team-confirm-{AIMAN}-cancel").click()
            await expect(page.get_by_label("Role for aiman@example.test")).to_have_value(
                "tenant_admin"
            )
            await expect(page.get_by_test_id(f"team-card-{AIMAN}-role")).to_have_text(
                "Tenant admin"
            )

    await with_browser(body)


async def test_disable_access_confirm_mentions_sign_out_and_acts_once(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with team_page(browser, site, world) as page:
            await open_card(page, SITI)
            # One button changes its name between Disable access and Enable access.
            trigger = page.get_by_test_id(f"team-access-{SITI}")
            await expect(trigger).to_have_text("Disable access")
            confirm = page.get_by_test_id(f"team-confirm-{SITI}")

            await trigger.click()
            await expect(confirm).to_be_visible()
            await expect(confirm).to_contain_text(f"Siti Aminah {SIGNED_OUT}")
            await expect(confirm).to_contain_text("until you enable access again")
            await expect(trigger).to_have_attribute("aria-expanded", "true")
            # A deliberate click on Disable access puts the safe answer in focus.
            assert await focused(page) == f"team-confirm-{SITI}-cancel"
            assert world.sent("PATCH", "team/") == []
            await expect(page.get_by_test_id(f"team-card-{SITI}-status")).to_have_text("Active")

            # Cancel and Escape both return to the Disable access button without sending.
            await page.get_by_test_id(f"team-confirm-{SITI}-cancel").click()
            await expect(confirm).to_have_count(0)
            assert await focused(page) == f"team-access-{SITI}"
            await trigger.click()
            await expect(confirm).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(confirm).to_have_count(0)
            assert await focused(page) == f"team-access-{SITI}"
            assert world.sent("PATCH", "team/") == []

            # A double click on the confirm button sends one change.
            world.hold = asyncio.Event()
            await trigger.click()
            yes = page.get_by_test_id(f"team-confirm-{SITI}-yes")
            await yes.dblclick()
            await expect(yes).to_be_disabled()
            await page.wait_for_timeout(300)
            assert len(world.sent("PATCH", "team/")) == 1, world.calls
            # While it is in flight Cancel does nothing and the question stays up.
            await page.get_by_test_id(f"team-confirm-{SITI}-cancel").click(force=True)
            await expect(confirm).to_be_visible()
            world.hold.set()
            await expect(confirm).to_have_count(0)
            sent = world.sent("PATCH", "team/")
            assert [(c["path"], c["body"]) for c in sent] == [
                (f"team/{SITI}", {"is_enabled": False})
            ]
            await expect(page.get_by_test_id(f"team-card-{SITI}-status")).to_have_text("Disabled")
            await expect(trigger).to_have_text("Enable access")
            assert await focused(page) == f"team-access-{SITI}"
            world.hold = None

            # Enabling again asks nothing: it is one click.
            await trigger.click()
            await expect(page.get_by_test_id(f"team-card-{SITI}-status")).to_have_text("Active")
            await expect(confirm).to_have_count(0)
            assert [c["body"] for c in world.sent("PATCH", "team/")] == [
                {"is_enabled": False},
                {"is_enabled": True},
            ]
            assert await focused(page) == f"team-access-{SITI}"

    await with_browser(body)


async def test_revoke_sessions_shows_confirmation(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with team_page(browser, site, world, platform=True) as page:
            details = await open_card(page, SITI)
            revoke = details.get_by_role("button", name="Revoke sessions")
            await expect(page.get_by_test_id(f"team-notice-{SITI}")).to_have_count(0)
            await revoke.click()
            notice = page.get_by_test_id(f"team-notice-{SITI}")
            await expect(notice).to_have_text(
                "Siti Aminah has been signed out everywhere and will need to sign in again."
            )
            sent = world.sent("POST", "admin/users/")
            assert [c["path"] for c in sent] == [f"admin/users/{SITI}/revoke-sessions"]
            assert await focused(page) == f"team-revoke-{SITI}"
            # The message belongs to that card only, and the next action clears it.
            await expect(page.get_by_test_id(f"team-notice-{AIMAN}")).to_have_count(0)
            await details.get_by_role("button", name="Disable access").click()
            await expect(notice).to_have_count(0)
            await page.keyboard.press("Escape")

            # A refusal is said in the same place and no success message appears.
            world.refuse = (404, "user not found")
            await revoke.click()
            await expect(page.get_by_test_id(f"team-notice-{SITI}")).to_have_count(0)
            await expect(page.get_by_test_id(f"team-card-{SITI}-error")).to_have_text(
                "user not found"
            )

    await with_browser(body)


async def test_invite_opens_in_a_drawer_and_the_form_still_works(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with team_page(browser, site, world) as page:
            open_button = page.get_by_test_id("team-invite-open")
            await expect(open_button).to_have_text("Invite someone")
            await expect(page.get_by_label("Display name")).to_have_count(0)
            await open_button.click()
            drawer = page.get_by_role("dialog", name="Invite a member")
            await expect(drawer).to_be_visible()
            # The existing form, unchanged, with its labels.
            await expect(drawer.get_by_label("Display name")).to_be_visible()
            await expect(drawer.get_by_label("Email", exact=True)).to_be_visible()
            await expect(drawer.get_by_label("Tenant role")).to_have_value("tenant_staff")
            await expect(drawer.get_by_role("button", name="Create invitation")).to_be_enabled()
            # No HR package here, so no HR access choice appears.
            await expect(drawer.get_by_test_id("invite-hr-access")).to_have_count(0)

            # Escape closes it, keeps what was typed and returns to the button that opened it.
            await drawer.get_by_label("Display name").fill("Nora Hassan")
            await page.keyboard.press("Escape")
            await expect(drawer).to_have_count(0)
            assert await focused(page) == "team-invite-open"
            await open_button.click()
            await expect(drawer.get_by_label("Display name")).to_have_value("Nora Hassan")

            # A refusal is said inside the drawer, once, and the drawer stays open.
            await drawer.get_by_label("Email", exact=True).fill("nora@example.test")
            world.refuse = (409, "email already invited")
            await drawer.get_by_role("button", name="Create invitation").click()
            await expect(drawer.get_by_test_id("team-invite-error")).to_have_text(
                "email already invited"
            )
            await expect(page.get_by_text("email already invited")).to_have_count(1)

            # Success closes the drawer, shows the delivery message and lists the invitation.
            await drawer.get_by_role("button", name="Create invitation").click()
            await expect(drawer).to_have_count(0)
            await expect(page.get_by_test_id("invite-delivery")).to_have_text(
                "Invitation emailed to nora@example.test"
            )
            await expect(page.get_by_text("email already invited")).to_have_count(0)
            await expect(page.get_by_test_id("invite-delivery")).to_be_in_viewport()
            sent = world.sent("POST", "team/invites")
            assert [c["body"] for c in sent][-1] == {
                "client_id": TENANT,
                "email": "nora@example.test",
                "display_name": "Nora Hassan",
                "role": "tenant_staff",
            }
            await expect(page.get_by_text("nora@example.test").first).to_be_visible()
            await expect(page.get_by_role("button", name="Hide message")).to_be_visible()
            await expect(page.get_by_role("button", name="Revoke link").first).to_be_visible()
            # The form starts empty again.
            await open_button.click()
            await expect(drawer.get_by_label("Display name")).to_have_value("")
            await drawer.get_by_role("button", name="Close").click()
            await expect(drawer).to_have_count(0)
            assert await focused(page) == "team-invite-open"

    await with_browser(body)


async def test_platform_members_page_uses_the_same_cards(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with team_page(browser, site, world, platform=True, wait=None) as page:
            await expect(page.get_by_role("heading", name="Tenant members")).to_be_visible()
            await expect(page.get_by_label("Selected tenant")).to_be_visible()
            await page.locator("#selected-tenant").select_option("")
            await expect(page.get_by_text("Select a tenant to manage its members.")).to_be_visible()
            await expect(page.get_by_test_id("team-invite-open")).to_have_count(0)
            await page.locator("#selected-tenant").select_option(TENANT)
            await expect(page.get_by_test_id("team-members")).to_be_visible()
            await expect(page.get_by_test_id("team-members").get_by_role("listitem")).to_have_count(
                3
            )
            await expect(page.get_by_test_id(f"team-card-{SITI}-role")).to_have_text("Tenant staff")
            await expect(page.get_by_test_id(f"team-card-{OMAR}-status")).to_have_text("Disabled")
            details = await open_card(page, SITI)
            # The platform tools sit in the same arrow, with their original labels.
            await expect(details.get_by_role("button", name="Issue reset")).to_be_visible()
            await expect(details.get_by_role("button", name="Revoke sessions")).to_be_visible()
            await expect(details.get_by_role("button", name="Disable access")).to_be_visible()

            # Issue reset shows its link at the top, where the page can see it.
            await details.get_by_role("button", name="Issue reset").click()
            await expect(page.get_by_test_id("invite-delivery")).to_contain_text(
                "siti@example.test"
            )
            await expect(page.locator("#issued-link")).to_have_value(
                re.compile(r"/invite#token=synthetic-reset-token$")
            )
            await expect(page.get_by_test_id("team-link-panel")).to_be_in_viewport()

            # Inviting works from the same drawer and sends the chosen company.
            await page.get_by_test_id("team-invite-open").click()
            drawer = page.get_by_role("dialog", name="Invite a member")
            await drawer.get_by_label("Display name").fill("Rina Ali")
            await drawer.get_by_label("Email", exact=True).fill("rina@example.test")
            await drawer.get_by_role("button", name="Create invitation").click()
            await expect(drawer).to_have_count(0)
            sent = world.sent("POST", "team/invites")
            assert sent[-1]["body"]["client_id"] == TENANT

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_team_at_five_widths_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        world = World()
        world.members[0]["display_name"] = "Aiman bin Yusof bin Abdul Rahman al-Haj"
        world.members[0]["email"] = (
            "aiman.yusof.abdul.rahman.alhaj.longname@perniagaan-serbaneka.example.test"
        )
        async with team_page(browser, site, world, theme=theme, platform=True) as page:
            await open_card(page, SITI)
            await open_card(page, AIMAN)
            await page.get_by_label("Role for siti@example.test").select_option("read_only")
            await expect(page.get_by_test_id(f"team-confirm-{SITI}")).to_be_visible()
            await page.get_by_test_id(f"team-card-{OMAR}-toggle").click()
            for invite in world.invites:
                await page.get_by_test_id(f"team-invitation-{invite['id']}-toggle").click()
            # The floating assistant button sits bottom right on every page: stand one in.
            await page.evaluate(
                """() => {
                    const pill = document.createElement('button');
                    pill.id = 'dummy-dock';
                    pill.textContent = 'Assistant';
                    pill.style.cssText = 'position:fixed;right:20px;bottom:20px;'
                        + 'z-index:40;width:132px;height:52px';
                    document.body.appendChild(pill);
                }"""
            )
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await page.wait_for_timeout(150)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (theme, width, measured)
                outside = await page.evaluate(
                    """() => [...document.querySelectorAll('[data-testid^="team-"]')].filter(
                        e => e.getBoundingClientRect().width > 0
                            && e.getBoundingClientRect().right > window.innerWidth + 0.5
                    ).map(e => e.dataset.testid)"""
                )
                assert not outside, (theme, width, outside)
                # With the page scrolled to its end, no button is under the dummy dock button.
                await page.evaluate(
                    "() => window.scrollTo(0, document.documentElement.scrollHeight)"
                )
                await page.evaluate(
                    "() => { const m = document.querySelector('main');"
                    " m?.scrollTo(0, m.scrollHeight); }"
                )
                await page.wait_for_timeout(100)
                covered = await page.evaluate(
                    """() => {
                        const out = [];
                        for (const b of document.querySelectorAll('main button, main a')) {
                            const r = b.getBoundingClientRect();
                            if (r.width === 0 || r.bottom < 0 || r.top > innerHeight) continue;
                            const x = r.left + r.width / 2, y = r.top + r.height / 2;
                            const hit = document.elementFromPoint(x, y);
                            if (hit && hit.id === 'dummy-dock') out.push(b.textContent.trim());
                        }
                        return out;
                    }"""
                )
                assert not covered, (theme, width, covered)
                await page.evaluate("() => window.scrollTo(0, 0)")
                await page.evaluate("() => document.querySelector('main')?.scrollTo(0, 0)")

    await with_browser(body)


ME = "22222222-2222-4222-8222-222222222222"


async def test_changing_your_own_access_says_you_will_be_signed_out(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = World()
        me = make_member(ME, "Synthetic User", "synthetic@example.test", "tenant_admin")
        world.members.append(me)
        async with team_page(browser, site, world) as page:
            await open_card(page, ME)
            await page.get_by_test_id(f"team-access-{ME}").click()
            confirm = page.get_by_test_id(f"team-confirm-{ME}")
            await expect(confirm).to_contain_text(
                "You will be signed out of this company straight away"
            )
            await expect(confirm).to_contain_text("another administrator enables your access again")
            await page.get_by_test_id(f"team-confirm-{ME}-cancel").click()
            await page.get_by_test_id(f"team-role-{ME}").select_option("tenant_staff")
            await expect(confirm).to_contain_text(
                "You will be signed out of this company straight away and will need to sign "
                "in again."
            )
            assert world.sent("PATCH", "team/") == []
            # Someone else's card still names them.
            await open_card(page, SITI)
            await page.get_by_test_id(f"team-access-{SITI}").click()
            await expect(page.get_by_test_id(f"team-confirm-{SITI}")).to_contain_text(
                f"Siti Aminah {SIGNED_OUT}"
            )

    await with_browser(body)


async def test_invite_drawer_starts_on_display_name_and_says_a_link_will_be_created(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = World()
        async with team_page(browser, site, world) as page:
            await page.get_by_test_id("team-invite-open").click()
            drawer = page.get_by_role("dialog", name="Invite a member")
            await expect(drawer).to_be_visible()
            await expect(drawer.get_by_label("Display name")).to_be_focused()
            await expect(drawer).to_contain_text("A link to join this company will be created.")
            await expect(drawer).not_to_contain_text("They will get a link")

    await with_browser(body)


async def test_focus_stays_on_a_control_that_remains_after_revoke_link_and_hide(
    site: Site,
) -> None:
    async def body(browser: Any) -> None:
        world = World()
        lina = "77777777-7777-4777-8777-000000000001"
        async with team_page(browser, site, world) as page:
            # Revoke link in the invitations list: the arrow of the same row keeps focus.
            await page.get_by_test_id(f"team-invitation-{lina}-toggle").click()
            await (
                page.get_by_test_id(f"team-invitation-{lina}-details")
                .get_by_role("button", name="Revoke link")
                .click()
            )
            await expect(page.get_by_test_id(f"team-invitation-{lina}-toggle")).to_be_focused()
            assert world.sent("DELETE", "admin/invites/")
            # The link panel just shown: Hide message and Revoke link hand focus to Invite someone.
            await page.get_by_test_id("team-invite-open").click()
            await page.get_by_label("Display name").fill("Nora Hassan")
            await page.get_by_label("Email", exact=True).fill("nora@example.test")
            await page.get_by_role("button", name="Create invitation").click()
            await expect(page.get_by_test_id("team-link-panel")).to_be_visible()
            await page.get_by_role("button", name="Hide message").click()
            await expect(page.get_by_test_id("team-link-panel")).to_have_count(0)
            assert await focused(page) == "team-invite-open"
            await page.get_by_test_id("team-invite-open").click()
            await page.get_by_label("Display name").fill("Omar Tan")
            await page.get_by_label("Email", exact=True).fill("omar2@example.test")
            await page.get_by_role("button", name="Create invitation").click()
            panel = page.get_by_test_id("team-link-panel")
            await expect(panel).to_be_visible()
            await panel.get_by_role("button", name="Revoke link").click()
            await expect(panel).to_have_count(0)
            assert await focused(page) == "team-invite-open"

    await with_browser(body)
