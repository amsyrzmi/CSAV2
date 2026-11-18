"""Real browser checks for the template editor inside the Processes tab (HR processes console,
Task 5).

The staged console runs from the copy `test_services_redesign_browser.site` builds, as in
`test_hr_processes_browser.py`. Playwright answers every browser-side `/console/v1/**` call from
`TemplatesWorld`: the Processes company with a fuller set of templates (rules, steps, two that
tie) and six active people the preview counts. The template routes come from the shared stub
`tests/hr_process_world.py`. Nadia (the HR owner) edits; Lina has no HR duties. Any call the stub
does not expect fails the test. All data is synthetic.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from csa.hr.default_templates import DEFAULT_TEMPLATES
from playwright.async_api import Locator, Page, expect

from tests import test_services_redesign_browser as services
from tests.hr_process_world import DEPARTMENT, TEMPLATE, Template
from tests.test_hr_access_browser import EMPLOYEE, MANAGER, OWNER, STAFF
from tests.test_hr_processes_browser import (
    LIST,
    SIDEWAYS,
    ProcessesWorld,
    focus_place,
    processes_page,
)
from tests.test_services_browser import TENANT, WAIT
from tests.test_services_redesign_browser import Site, with_browser
from tests.test_team_browser import THEMES, WIDTHS, overflow

pytestmark = pytest.mark.asyncio

# The staged copy of the console is built once per file by the services fixture.
site = services.site

TEMPLATES = f"{LIST}/templates"
NEW = f"{TEMPLATES}/new"
SABAH = "66666666-6666-4666-8666-000000000105"
CONTRACT = "66666666-6666-4666-8666-000000000106"
WAREHOUSE = TEMPLATE["warehouse"]
LEAVING = TEMPLATE["leaving"]
CONFLICT = (
    "Someone else changed this template while you were editing. "
    "Your changes are still here and have not been saved."
)
UNSAVABLE = "This text has a character we can't save. Remove it and try again."
FORMER = "This person is no longer in your company list. Choose someone else."
UNCERTAIN = "We couldn't confirm that. Choose Save again; it won't be done twice."
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CODES = ("full_time", "start_date", "last_day", "probation_end", "peninsular_labuan", "_")


def full_step(title: str, **changes: Any) -> dict[str, Any]:
    """A `TemplateStepOut` without its ordinal, as the stub stores it."""
    return {
        "title_en": title,
        "title_ms": None,
        "description_en": None,
        "description_ms": None,
        "owner_role": "hr",
        "owner_employee_id": None,
        "anchor": "start_date",
        "due_offset_days": 0,
        "required": True,
        "approval_by": None,
        "auto_check": None,
        "reference_note": None,
    } | changes


WAREHOUSE_STEPS = [
    full_step(
        "Sign the employment contract",
        title_ms="Tandatangan kontrak pekerjaan",
        description_en="Read the contract.\nSign the last page.",
        owner_role="employee",
        approval_by="manager",
        reference_note="HR shared folder",
    ),
    full_step(
        "Set up the payroll record",
        due_offset_days=-3,
        required=False,
        auto_check="document_uploaded:contract",
    ),
    full_step(
        "Meet your buddy",
        owner_role="person",
        owner_employee_id=EMPLOYEE[MANAGER],
        anchor="probation_end",
        due_offset_days=5,
        approval_by="hr",
    ),
]


class TemplatesWorld(ProcessesWorld):
    """The Processes company with rules and full steps on its templates, two onboarding
    templates that tie (Sabah and Contract), legal entities on the people read, and six active
    people for the preview."""

    def __init__(self) -> None:
        super().__init__()
        world = self.processes
        warehouse = world.template(WAREHOUSE)
        assert warehouse
        warehouse.rules = {"department_id": DEPARTMENT["warehouse"], "employment_type": "full_time"}
        warehouse.details = [dict(step) for step in WAREHOUSE_STEPS]
        warehouse.steps = tuple(step["title_en"] for step in WAREHOUSE_STEPS)
        leaving = world.template(LEAVING)
        assert leaving
        leaving.details = [full_step("Return the laptop", owner_role="employee", anchor="last_day")]
        world.templates += [
            Template(
                SABAH,
                "onboarding",
                "Sabah onboarding",
                rules={"region": "sabah"},
                created_at="2026-10-02T02:00:00Z",
                steps=("Register with the Sabah office",),
            ),
            Template(
                CONTRACT,
                "onboarding",
                "Contract onboarding",
                rules={"employment_type": "contract"},
                created_at="2026-10-03T02:00:00Z",
                steps=("Sign the fixed-term contract",),
            ),
        ]
        cekap, mawar = "Syarikat Cekap Sdn Bhd", "Kedai Mawar Enterprise"
        warehouse_id, operations = DEPARTMENT["warehouse"], DEPARTMENT["operations"]
        world.people = [
            {"department_id": d, "employment_type": t, "legal_entity_reference": e, "region": r}
            for d, t, e, r in (
                (warehouse_id, "full_time", cekap, "peninsular_labuan"),
                (warehouse_id, "full_time", cekap, "peninsular_labuan"),
                (operations, "full_time", cekap, "peninsular_labuan"),
                (operations, "contract", mawar, "sabah"),
                (warehouse_id, "part_time", mawar, "sabah"),
                (warehouse_id, "intern", None, "sarawak"),
            )
        ]

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        status, data = super().answer(caller, method, path, params, body)
        if path == "hr/people" and method == "GET" and status == 200:
            entities = ("Syarikat Cekap Sdn Bhd", "Kedai Mawar Enterprise", None)
            for index, person in enumerate(data["items"]):
                entity = entities[index % len(entities)]
                person["employment_profile"] = entity and {
                    "region": "peninsular_labuan",
                    "legal_entity_reference": entity if index < 3 else entity.upper(),
                    "employment_category": "",
                }
        return status, data

    def template_calls(self, method: str, path: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method and c["path"] == path]

    def previews(self) -> list[dict[str, Any]]:
        return [c["body"] for c in self.template_calls("POST", "hr/templates/preview")]


def editor(page: Page) -> Locator:
    return page.get_by_test_id("hr-template-editor")


def template_list(page: Page) -> Locator:
    return page.get_by_test_id("hr-template-list")


def template_path(template_id: str) -> str:
    return f"{TEMPLATES}/{template_id}"


def steps(page: Page) -> Locator:
    return editor(page).locator("[data-testid=hr-template-step]")


def step_row(page: Page, number: int) -> Locator:
    return editor(page).get_by_role("group", name=re.compile(rf"^Step {number}:"))


def notices(page: Page) -> Locator:
    return page.get_by_test_id("hr-template-outcome")


async def step_titles(page: Page) -> list[str]:
    return await steps(page).evaluate_all(
        "(rows) => rows.map((row) => row.querySelector('input[data-field=title_en]').value)"
    )


async def wait_for(world: TemplatesWorld, page: Page, method: str, path: str, count: int) -> None:
    for _ in range(100):
        if len(world.template_calls(method, path)) >= count:
            return
        await page.wait_for_timeout(100)
    raise AssertionError((method, path, count, world.template_calls(method, path)))


def assert_template_write(call: dict[str, Any], method: str, path: str, body: Any) -> None:
    assert call["method"] == method, call
    assert call["path"] == path, call
    assert call["params"] == {"client_id": TENANT}, call
    assert call["body"] == body, call
    assert call["key"], call
    assert call["csrf"] == "synthetic-csrf", call


async def fill_step(row: Locator, **values: Any) -> None:
    """Fill a step row's fields by their labels."""
    labels = {
        "title_en": "Title (English)",
        "title_ms": "Title (Malay)",
        "description_en": "Description (English)",
        "description_ms": "Description (Malay)",
        "reference_note": "Reference note",
        "due_offset_days": "Days",
    }
    selects = {
        "owner_role": "Owner",
        "owner_employee_id": "Employee",
        "anchor": "Counted from",
        "approval_by": "Approval by",
        "auto_check": "Automatic check",
    }
    for name, value in values.items():
        if name in labels:
            await row.get_by_label(labels[name], exact=True).fill(value)
        elif name in selects:
            await row.get_by_label(selects[name], exact=True).select_option(value)
        elif name == "required":
            await row.get_by_label("Required", exact=True).set_checked(value)
        else:
            raise AssertionError(name)


def leave_anyway(page: Page) -> None:
    """The next browser "leave this page?" question (unsaved changes) is answered yes."""
    page.once("dialog", lambda dialog: dialog.accept())


async def test_list_says_who_each_template_applies_to_and_marks_ties(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        async with processes_page(browser, site, world, TEMPLATES, ready=template_list) as page:
            row = lambda template_id: page.get_by_test_id(f"hr-template-row-{template_id}")  # noqa: E731
            warehouse = row(WAREHOUSE)
            await expect(warehouse).to_contain_text("Warehouse onboarding", timeout=WAIT)
            for text in (
                "Onboarding",
                "Department: Warehouse · Type: Full-time",
                "3 steps",
                "Active",
            ):
                await expect(warehouse).to_contain_text(text)
            await expect(warehouse).not_to_contain_text("May tie")
            await expect(row(SABAH)).to_contain_text("Region: Sabah")
            await expect(row(SABAH)).to_contain_text("May tie with Contract onboarding")
            await expect(row(CONTRACT)).to_contain_text("May tie with Sabah onboarding")
            await expect(row(LEAVING)).to_contain_text("Everyone")
            await expect(row(TEMPLATE["old"])).to_contain_text("Inactive")
            # Custom templates are picked by hand: they never tie.
            await expect(row(TEMPLATE["transfer"])).not_to_contain_text("May tie")
            text = await template_list(page).inner_text()
            assert not UUID.search(text), text
            assert not [code for code in CODES if code in text], text
            link = warehouse.get_by_role("link", name="Warehouse onboarding")
            await expect(link).to_have_attribute("href", template_path(WAREHOUSE))
            new = template_list(page).get_by_role("link", name="New template")
            await expect(new).to_have_attribute("href", NEW)
            await link.click()
            await page.wait_for_url(f"{site.base}{template_path(WAREHOUSE)}")
            await expect(editor(page).get_by_label("Name", exact=True)).to_have_value(
                "Warehouse onboarding", timeout=WAIT
            )

    await with_browser(body)


async def test_an_empty_list_explains_templates(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        world.processes.templates = []
        async with processes_page(browser, site, world, TEMPLATES, ready=template_list) as page:
            await expect(template_list(page).get_by_text("No templates yet.")).to_be_visible(
                timeout=WAIT
            )
            await expect(template_list(page)).to_contain_text("A template lists the steps")

    await with_browser(body)


async def test_create_an_onboarding_template_with_three_steps(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        async with processes_page(browser, site, world, NEW, ready=editor) as page:
            form = editor(page)
            await form.get_by_label("Name", exact=True).fill("Warehouse onboarding 2027")
            await form.get_by_label("Department", exact=True).select_option(label="Warehouse")
            await form.get_by_label("Employment type", exact=True).select_option("full_time")
            entity = form.get_by_label("Legal entity", exact=True)
            values = await form.locator("datalist option").evaluate_all(
                "(items) => items.map((item) => item.value)"
            )
            assert values == ["Kedai Mawar Enterprise", "Syarikat Cekap Sdn Bhd"], values
            await entity.fill("Syarikat Cekap Sdn Bhd")
            first = step_row(page, 1)
            await fill_step(
                first,
                title_en="Sign the employment contract",
                title_ms="Tandatangan kontrak pekerjaan",
                description_en="Read the contract.\nSign the last page.",
                description_ms="Baca kontrak.\nTandatangan halaman akhir.",
                reference_note="HR shared folder",
                owner_role="employee",
                approval_by="manager",
            )
            await expect(first).to_contain_text("On the start date")
            await form.get_by_role("button", name="Add step").click()
            second = step_row(page, 2)
            await fill_step(
                second,
                title_en="Set up the payroll record",
                due_offset_days="-3",
                required=False,
                auto_check="document_uploaded:contract",
            )
            await expect(second).to_contain_text("3 days before the start date")
            await form.get_by_role("button", name="Add step").click()
            third = step_row(page, 3)
            await fill_step(third, title_en="Meet your buddy", owner_role="person")
            await fill_step(
                third,
                owner_employee_id=EMPLOYEE[MANAGER],
                anchor="probation_end",
                due_offset_days="5",
                approval_by="hr",
            )
            await expect(third).to_contain_text("5 days after the probation end date")
            await form.get_by_role("button", name="Save", exact=True).click()
            await wait_for(world, page, "POST", "hr/templates", 1)
            created = world.template_calls("POST", "hr/templates")
            assert len(created) == 1, created
            assert_template_write(
                created[0],
                "POST",
                "hr/templates",
                {
                    "kind": "onboarding",
                    "name": "Warehouse onboarding 2027",
                    "rules": {
                        "department_id": DEPARTMENT["warehouse"],
                        "employment_type": "full_time",
                        "legal_entity_reference": "Syarikat Cekap Sdn Bhd",
                        "region": None,
                    },
                    "steps": [
                        full_step(
                            "Sign the employment contract",
                            title_ms="Tandatangan kontrak pekerjaan",
                            description_en="Read the contract.\nSign the last page.",
                            description_ms="Baca kontrak.\nTandatangan halaman akhir.",
                            reference_note="HR shared folder",
                            owner_role="employee",
                            approval_by="manager",
                        ),
                        full_step(
                            "Set up the payroll record",
                            due_offset_days=-3,
                            required=False,
                            auto_check="document_uploaded:contract",
                        ),
                        full_step(
                            "Meet your buddy",
                            owner_role="person",
                            owner_employee_id=EMPLOYEE[MANAGER],
                            anchor="probation_end",
                            due_offset_days=5,
                            approval_by="hr",
                        ),
                    ],
                },
            )
            # The new template opens on its own page with the saved message, and the
            # description's line breaks come back from the read.
            new_id = world.processes.templates[-1].template_id
            await page.wait_for_url(f"{site.base}{template_path(new_id)}")
            await expect(page.get_by_test_id("hr-template-notice")).to_have_text(
                "Template saved.", timeout=WAIT
            )
            await expect(notices(page)).to_be_focused()
            await expect(
                step_row(page, 1).get_by_label("Description (English)", exact=True)
            ).to_have_value("Read the contract.\nSign the last page.")

    await with_browser(body)


async def test_reorder_remove_and_add_change_the_update(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        path = template_path(WAREHOUSE)
        async with processes_page(browser, site, world, path, ready=editor) as page:
            await expect(steps(page)).to_have_count(3, timeout=WAIT)
            assert await step_titles(page) == [
                "Sign the employment contract",
                "Set up the payroll record",
                "Meet your buddy",
            ]
            await step_row(page, 3).get_by_role("button", name="Move up").click()
            await step_row(page, 2).get_by_role("button", name="Move up").click()
            assert await step_titles(page) == [
                "Meet your buddy",
                "Sign the employment contract",
                "Set up the payroll record",
            ]
            await step_row(page, 3).get_by_role("button", name="Remove step").click()
            await editor(page).get_by_role("button", name="Add step").click()
            await fill_step(step_row(page, 3), title_en="Collect the safety kit")
            assert await step_titles(page) == [
                "Meet your buddy",
                "Sign the employment contract",
                "Collect the safety kit",
            ]
            names = await steps(page).evaluate_all(
                "(rows) => rows.map((row) => row.querySelector('legend').textContent)"
            )
            assert names == [
                "Step 1: Meet your buddy",
                "Step 2: Sign the employment contract",
                "Step 3: Collect the safety kit",
            ], names
            await editor(page).get_by_role("button", name="Save", exact=True).click()
            await wait_for(world, page, "PUT", f"hr/templates/{WAREHOUSE}", 1)
            (put,) = world.template_calls("PUT", f"hr/templates/{WAREHOUSE}")
            assert_template_write(
                put,
                "PUT",
                f"hr/templates/{WAREHOUSE}",
                {
                    "expected_version": 2,
                    "name": "Warehouse onboarding",
                    "rules": {
                        "department_id": DEPARTMENT["warehouse"],
                        "employment_type": "full_time",
                        "legal_entity_reference": None,
                        "region": None,
                    },
                    # The unchanged description keeps its line break.
                    "steps": [
                        WAREHOUSE_STEPS[2],
                        WAREHOUSE_STEPS[0],
                        full_step("Collect the safety kit"),
                    ],
                },
            )
            await expect(page.get_by_test_id("hr-template-notice")).to_have_text(
                "Template saved.", timeout=WAIT
            )
            await expect(notices(page)).to_be_focused()

    await with_browser(body)


async def test_clearing_every_rule_sends_null_rules(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        path = template_path(SABAH)
        async with processes_page(browser, site, world, path, ready=editor) as page:
            region = editor(page).get_by_label("Region", exact=True)
            await expect(region).to_have_value("sabah", timeout=WAIT)
            await region.select_option("")
            await editor(page).get_by_role("button", name="Save", exact=True).click()
            await wait_for(world, page, "PUT", f"hr/templates/{SABAH}", 1)
            (put,) = world.template_calls("PUT", f"hr/templates/{SABAH}")
            assert "rules" in put["body"] and put["body"]["rules"] is None, put
            assert put["body"]["expected_version"] == 1, put

    await with_browser(body)


async def test_the_41st_step_is_refused(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        async with processes_page(browser, site, world, NEW, ready=editor) as page:
            add = editor(page).get_by_role("button", name="Add step")
            for _ in range(39):
                await add.click()
            await expect(steps(page)).to_have_count(40)
            await expect(
                editor(page).get_by_text("A template has at most 40 steps.")
            ).to_have_count(0)
            await add.click()
            await expect(
                editor(page).get_by_text("A template has at most 40 steps.")
            ).to_be_visible()
            await expect(steps(page)).to_have_count(40)
            # The only step left cannot be removed.
            for _ in range(39):
                await step_row(page, 1).get_by_role("button", name="Remove step").click()
            await expect(steps(page)).to_have_count(1)
            await expect(
                step_row(page, 1).get_by_role("button", name="Remove step")
            ).to_be_disabled()

    await with_browser(body)


async def anchor_options(row: Locator) -> list[str]:
    return (
        await row.get_by_label("Counted from", exact=True)
        .locator("option")
        .evaluate_all("(items) => items.map((item) => item.textContent)")
    )


async def test_the_anchor_select_offers_only_the_dates_of_the_kind(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        async with processes_page(browser, site, world, NEW, ready=editor) as page:
            row = step_row(page, 1)
            assert await anchor_options(row) == ["Start date", "Probation end"]
            await fill_step(row, anchor="probation_end", due_offset_days="5")
            await editor(page).get_by_label("Kind", exact=True).select_option("offboarding")
            assert await anchor_options(row) == ["Last day"]
            await expect(row.get_by_label("Counted from", exact=True)).to_have_value("last_day")
            await expect(row).to_contain_text("5 days after the last day")
            await editor(page).get_by_label("Kind", exact=True).select_option("custom")
            assert await anchor_options(row) == ["Start date", "Last day", "Probation end"]
            # A saved template's kind is fixed.
            leave_anyway(page)
            await page.goto(f"{site.base}{template_path(LEAVING)}")
            await expect(editor(page).get_by_text("Offboarding", exact=True)).to_be_visible(
                timeout=WAIT
            )
            await expect(editor(page).get_by_label("Kind", exact=True)).to_have_count(0)
            assert await anchor_options(step_row(page, 1)) == ["Last day"]

    await with_browser(body)


async def test_preview_follows_the_rules_and_shows_ties(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        async with processes_page(browser, site, world, NEW, ready=editor) as page:
            preview = page.get_by_test_id("hr-template-preview")
            await expect(preview).to_contain_text("This applies to 6 people now.", timeout=WAIT)
            assert world.previews() == [{"kind": "onboarding", "rules": None}]
            await editor(page).get_by_label("Region", exact=True).select_option("sabah")
            # Debounced: nothing is asked at once.
            await page.wait_for_timeout(150)
            assert len(world.previews()) == 1, world.previews()
            await expect(preview).to_contain_text("This applies to 2 people now.", timeout=WAIT)
            assert world.previews()[-1] == {
                "kind": "onboarding",
                "rules": {
                    "department_id": None,
                    "employment_type": None,
                    "legal_entity_reference": None,
                    "region": "sabah",
                },
            }
            await expect(preview).to_contain_text("May tie with Sabah onboarding")
            await expect(preview).to_contain_text("May tie with Contract onboarding")
            await editor(page).get_by_label("Employment type", exact=True).select_option("intern")
            await expect(preview).to_contain_text("No one matches yet.", timeout=WAIT)
            await expect(preview).not_to_contain_text("May tie")
            # The rules are invalid while the legal entity is too long: no preview is asked.
            count = len(world.previews())
            await editor(page).get_by_label("Legal entity", exact=True).fill("x" * 201)
            await page.wait_for_timeout(700)
            assert len(world.previews()) == count, world.previews()
            # The editor of a saved template never lists the template itself.
            leave_anyway(page)
            await page.goto(f"{site.base}{template_path(SABAH)}")
            preview = page.get_by_test_id("hr-template-preview")
            await expect(preview).to_contain_text("May tie with Contract onboarding", timeout=WAIT)
            await expect(preview).to_contain_text("This applies to 2 people now.")
            await expect(preview).not_to_contain_text("Sabah onboarding")

    await with_browser(body)


async def stale_save(world: TemplatesWorld, page: Page) -> Locator:
    """Someone else saves the warehouse template, then Nadia saves her name change: the save is
    refused and the conflict notice (which takes focus) offers her choices."""
    name = editor(page).get_by_label("Name", exact=True)
    await expect(name).to_have_value("Warehouse onboarding", timeout=WAIT)
    world.processes.touch_template(WAREHOUSE, name="Warehouse onboarding (new)")
    await name.fill("My edit")
    await editor(page).get_by_role("button", name="Save", exact=True).click()
    conflict = page.get_by_test_id("hr-template-conflict")
    await expect(conflict).to_contain_text(CONFLICT, timeout=WAIT)
    await expect(conflict.get_by_role("button", name="Keep my changes")).to_be_enabled()
    await expect(notices(page)).to_be_focused()
    # The draft is still on screen.
    await expect(name).to_have_value("My edit")
    return conflict


async def test_a_stale_save_keeps_the_draft_and_keeping_it_replaces_theirs(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        put_path = f"hr/templates/{WAREHOUSE}"
        async with processes_page(
            browser, site, world, template_path(WAREHOUSE), ready=editor
        ) as page:
            conflict = await stale_save(world, page)
            await expect(page.get_by_test_id("hr-template-error")).to_have_count(0)
            # Their version was read beside the draft, after the refused save.
            calls = [(c["method"], c["path"]) for c in world.calls]
            refused = calls.index(("PUT", put_path))
            assert ("GET", put_path) in calls[refused:], calls[refused:]
            await conflict.get_by_role("button", name="Keep my changes").click()
            await expect(conflict).to_contain_text("Saving now replaces their version.")
            await expect(notices(page)).to_be_focused()
            # Someone saves once more before Nadia does: the notice comes back with that version.
            world.processes.touch_template(WAREHOUSE, name="Warehouse onboarding (newest)")
            save = editor(page).get_by_role("button", name="Save", exact=True)
            await save.click()
            await wait_for(world, page, "PUT", put_path, 2)
            await expect(conflict.get_by_role("button", name="Keep my changes")).to_be_visible(
                timeout=WAIT
            )
            await expect(notices(page)).to_be_focused()
            await conflict.get_by_role("button", name="Keep my changes").click()
            await save.click()
            await expect(page.get_by_test_id("hr-template-notice")).to_have_text(
                "Template saved.", timeout=WAIT
            )
            await expect(conflict).to_have_count(0)
            puts = world.template_calls("PUT", put_path)
            assert [p["body"]["expected_version"] for p in puts] == [2, 3, 4], puts
            # Every try sends the same draft: only the version moves.
            drafts = [{k: v for k, v in p["body"].items() if k != "expected_version"} for p in puts]
            assert drafts[0] == drafts[1] == drafts[2], drafts
            assert drafts[0]["name"] == "My edit", drafts
            assert drafts[0]["steps"] == WAREHOUSE_STEPS, drafts
            template = world.processes.template(WAREHOUSE)
            assert template and template.name == "My edit" and template.version == 5

    await with_browser(body)


async def test_a_stale_save_can_show_their_version_after_a_confirm(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        async with processes_page(
            browser, site, world, template_path(WAREHOUSE), ready=editor
        ) as page:
            conflict = await stale_save(world, page)
            name = editor(page).get_by_label("Name", exact=True)
            theirs = conflict.get_by_role("button", name="Show their version")
            await theirs.click()
            ask = page.get_by_test_id("hr-template-theirs")
            await expect(ask).to_contain_text("Discard your changes?")
            await ask.get_by_role("button", name="Keep editing").click()
            await expect(ask).to_have_count(0)
            await expect(theirs).to_be_focused()
            await expect(name).to_have_value("My edit")
            await theirs.click()
            await ask.get_by_role("button", name="Discard my changes").click()
            await expect(name).to_have_value("Warehouse onboarding (new)")
            await expect(page.get_by_test_id("hr-template-notice")).to_have_text(
                "Showing their version. Your changes were discarded."
            )
            await expect(notices(page)).to_be_focused()
            await expect(conflict).to_have_count(0)
            await editor(page).get_by_role("button", name="Save", exact=True).click()
            await wait_for(world, page, "PUT", f"hr/templates/{WAREHOUSE}", 2)
            puts = world.template_calls("PUT", f"hr/templates/{WAREHOUSE}")
            assert puts[1]["body"]["expected_version"] == 3, puts
            assert puts[1]["body"]["name"] == "Warehouse onboarding (new)", puts

    await with_browser(body)


async def test_a_stale_save_can_copy_the_draft_as_a_new_template(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        async with processes_page(
            browser, site, world, template_path(WAREHOUSE), ready=editor
        ) as page:
            conflict = await stale_save(world, page)
            await conflict.get_by_role("button", name="Show their version").click()
            ask = page.get_by_test_id("hr-template-theirs")
            await ask.get_by_role("button", name="Copy my version as new").click()
            await page.wait_for_url(f"{site.base}{NEW}")
            await expect(editor(page).get_by_label("Name", exact=True)).to_have_value(
                "Copy of My edit"
            )
            assert await step_titles(page) == [s["title_en"] for s in WAREHOUSE_STEPS]
            assert len(world.template_calls("PUT", f"hr/templates/{WAREHOUSE}")) == 1

    await with_browser(body)


async def test_a_pasted_tab_is_sent_as_a_space(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        path = template_path(LEAVING)
        async with processes_page(browser, site, world, path, ready=editor) as page:
            await expect(steps(page)).to_have_count(1, timeout=WAIT)
            # A bulleted list pasted from a word processor arrives with tabs and CRLF.
            await fill_step(
                step_row(page, 1),
                title_en="Return\tthe laptop",
                description_en="•\tThe laptop\r\n•\tThe charger",
            )
            await editor(page).get_by_role("button", name="Save", exact=True).click()
            await expect(page.get_by_test_id("hr-template-notice")).to_have_text(
                "Template saved.", timeout=WAIT
            )
            (put,) = world.template_calls("PUT", f"hr/templates/{LEAVING}")
            sent = put["body"]["steps"][0]
            assert sent["title_en"] == "Return the laptop", sent
            assert sent["description_en"] == "• The laptop\n• The charger", sent

    await with_browser(body)


async def test_a_character_the_api_refuses_is_marked_on_its_field(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        path = template_path(LEAVING)
        async with processes_page(browser, site, world, path, ready=editor) as page:
            await expect(steps(page)).to_have_count(1, timeout=WAIT)
            row = step_row(page, 1)
            await fill_step(row, reference_note="Locker \u0007 B")
            await editor(page).get_by_role("button", name="Save", exact=True).click()
            note = row.get_by_label("Reference note", exact=True)
            await expect(note).to_be_focused()
            await expect(note).to_have_accessible_description(UNSAVABLE)
            assert world.template_calls("PUT", f"hr/templates/{LEAVING}") == []

    await with_browser(body)


async def test_a_named_owner_who_left_is_marked_and_blocks_the_save(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        gone = "55555555-5555-4555-8555-000000000099"
        warehouse = world.processes.template(WAREHOUSE)
        assert warehouse and warehouse.details
        warehouse.details[2]["owner_employee_id"] = gone
        path = template_path(WAREHOUSE)
        async with processes_page(browser, site, world, path, ready=editor) as page:
            owner = step_row(page, 3).get_by_label("Employee", exact=True)
            await expect(owner).to_have_value(gone, timeout=WAIT)
            chosen = owner.locator("option:checked")
            await expect(chosen).to_have_text("Former employee")
            await expect(owner).to_have_accessible_description(FORMER, timeout=WAIT)
            await editor(page).get_by_role("button", name="Save", exact=True).click()
            await expect(owner).to_be_focused()
            assert world.template_calls("PUT", f"hr/templates/{WAREHOUSE}") == []
            await owner.select_option(EMPLOYEE[MANAGER])
            await expect(step_row(page, 3).get_by_text(FORMER)).to_have_count(0)
            await editor(page).get_by_role("button", name="Save", exact=True).click()
            await wait_for(world, page, "PUT", f"hr/templates/{WAREHOUSE}", 1)

    await with_browser(body)


async def test_a_failed_save_twice_keeps_focus_on_the_message(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        put_path = f"hr/templates/{WAREHOUSE}"
        world.processes.failures[f"PUT {put_path}"] = [
            (500, "synthetic_outage"),
            (500, "synthetic_outage"),
        ]
        async with processes_page(
            browser, site, world, template_path(WAREHOUSE), ready=editor
        ) as page:
            await editor(page).get_by_label("Name", exact=True).fill("Warehouse onboarding v3")
            save = editor(page).get_by_role("button", name="Save", exact=True)
            for attempt in (1, 2):
                await save.click()
                await wait_for(world, page, "PUT", put_path, attempt)
                await expect(page.get_by_test_id("hr-template-error")).to_have_text(
                    UNCERTAIN, timeout=WAIT
                )
                await expect(notices(page)).to_be_focused()
                assert await focus_place(page, "hr-template-outcome") == "dialog", attempt
            await save.click()
            await expect(page.get_by_test_id("hr-template-notice")).to_have_text(
                "Template saved.", timeout=WAIT
            )
            puts = world.template_calls("PUT", put_path)
            assert len(puts) == 3 and len({p["key"] for p in puts}) == 1, puts

    await with_browser(body)


async def test_deactivate_asks_first_and_sends_the_version(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        path = template_path(CONTRACT)
        async with processes_page(browser, site, world, path, ready=editor) as page:
            deactivate = editor(page).get_by_role("button", name="Deactivate")
            await deactivate.click(timeout=WAIT)
            ask = page.get_by_test_id("hr-template-dialog")
            await expect(ask).to_contain_text("Deactivate Contract onboarding?")
            await ask.get_by_role("button", name="Keep it").click()
            await expect(ask).to_have_count(0)
            await expect(deactivate).to_be_focused()
            assert world.template_calls("POST", f"hr/templates/{CONTRACT}/deactivate") == []
            await deactivate.click()
            await ask.get_by_role("button", name="Deactivate").click()
            await expect(page.get_by_test_id("hr-template-notice")).to_have_text(
                "Template deactivated. New processes no longer use it.", timeout=WAIT
            )
            (call,) = world.template_calls("POST", f"hr/templates/{CONTRACT}/deactivate")
            assert_template_write(
                call, "POST", f"hr/templates/{CONTRACT}/deactivate", {"expected_version": 1}
            )
            await expect(notices(page)).to_be_focused()
            await expect(editor(page).get_by_text("Inactive", exact=True)).to_be_visible()
            await expect(deactivate).to_have_count(0)
            # Saving can turn it on again.
            await editor(page).get_by_role("button", name="Save and activate").click()
            await wait_for(world, page, "PUT", f"hr/templates/{CONTRACT}", 1)
            (put,) = world.template_calls("PUT", f"hr/templates/{CONTRACT}")
            assert put["body"]["is_active"] is True and put["body"]["expected_version"] == 2, put
            await expect(editor(page).get_by_text("Active", exact=True)).to_be_visible(timeout=WAIT)

    await with_browser(body)


async def test_copy_as_new_opens_a_new_template_with_the_steps(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        path = template_path(WAREHOUSE)
        async with processes_page(browser, site, world, path, ready=editor) as page:
            await expect(steps(page)).to_have_count(3, timeout=WAIT)
            await editor(page).get_by_role("button", name="Copy as new").click()
            await page.wait_for_url(f"{site.base}{NEW}")
            name = editor(page).get_by_label("Name", exact=True)
            await expect(name).to_have_value("Copy of Warehouse onboarding")
            await expect(editor(page).get_by_label("Kind", exact=True)).to_have_value("onboarding")
            assert await step_titles(page) == [s["title_en"] for s in WAREHOUSE_STEPS]
            await editor(page).get_by_role("button", name="Save", exact=True).click()
            await wait_for(world, page, "POST", "hr/templates", 1)
            (call,) = world.template_calls("POST", "hr/templates")
            assert call["body"]["kind"] == "onboarding", call
            assert call["body"]["name"] == "Copy of Warehouse onboarding", call
            assert call["body"]["steps"] == WAREHOUSE_STEPS, call
            assert call["body"]["rules"] == {
                "department_id": DEPARTMENT["warehouse"],
                "employment_type": "full_time",
                "legal_entity_reference": None,
                "region": None,
            }, call
            # The original is untouched.
            assert world.template_calls("PUT", f"hr/templates/{WAREHOUSE}") == []

    await with_browser(body)


async def test_leaving_with_unsaved_changes_asks_first(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        path = template_path(WAREHOUSE)
        async with processes_page(browser, site, world, path, ready=editor) as page:
            views = page.get_by_role("navigation", name="Process pages")
            name = editor(page).get_by_label("Name", exact=True)
            await expect(name).to_have_value("Warehouse onboarding", timeout=WAIT)
            await name.fill("Warehouse onboarding (draft)")
            await views.get_by_role("link", name="Processes").click()
            guard = page.get_by_test_id("hr-template-discard")
            await expect(guard).to_contain_text("Discard your changes?")
            await guard.get_by_role("button", name="Keep editing").click()
            await expect(guard).to_have_count(0)
            assert page.url == f"{site.base}{path}"
            await expect(name).to_have_value("Warehouse onboarding (draft)")
            await views.get_by_role("link", name="Processes").click()
            await guard.get_by_role("button", name="Discard changes").click()
            await page.wait_for_url(f"{site.base}{LIST}")
            # An unchanged template leaves at once.
            await page.goto(f"{site.base}{path}")
            await expect(name).to_have_value("Warehouse onboarding", timeout=WAIT)
            await views.get_by_role("link", name="Templates").click()
            await page.wait_for_url(f"{site.base}{TEMPLATES}")
            await expect(guard).to_have_count(0)
            assert [c for c in world.calls if c["method"] == "PUT"] == []

    await with_browser(body)


async def test_client_side_checks_say_what_to_fix(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        async with processes_page(browser, site, world, NEW, ready=editor) as page:
            await fill_step(
                step_row(page, 1),
                due_offset_days="400",
                description_en="x" * 2001,
                owner_role="person",
            )
            await editor(page).get_by_role("button", name="Save", exact=True).click()
            for sentence in (
                "Give the template a name of at most 120 characters.",
                "Each step needs an English title of at most 160 characters.",
                "Give a whole number of days from -365 to 365.",
                "A description is at most 2000 characters.",
                "Choose an active person of this company for the step.",
            ):
                await expect(editor(page).get_by_text(sentence)).to_be_visible()
            await expect(editor(page).get_by_label("Name", exact=True)).to_be_focused()
            await expect(editor(page).get_by_label("Name", exact=True)).to_have_attribute(
                "aria-invalid", "true"
            )
            await editor(page).get_by_label("Name", exact=True).fill("x" * 121)
            await editor(page).get_by_role("button", name="Save", exact=True).click()
            await expect(
                editor(page).get_by_text("Give the template a name of at most 120 characters.")
            ).to_be_visible()
            assert world.template_calls("POST", "hr/templates") == []

    await with_browser(body)


async def test_a_read_only_member_sees_the_templates_without_controls(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        async with processes_page(
            browser, site, world, TEMPLATES, role="read_only", ready=template_list
        ) as page:
            await expect(page.get_by_test_id(f"hr-template-row-{WAREHOUSE}")).to_be_visible(
                timeout=WAIT
            )
            await expect(
                template_list(page).get_by_role("link", name="New template")
            ).to_have_count(0)
            await page.goto(f"{site.base}{template_path(WAREHOUSE)}")
            view = editor(page)
            await expect(view.get_by_text("Meet your buddy")).to_be_visible(timeout=WAIT)
            await expect(view).to_contain_text("Department: Warehouse · Type: Full-time")
            await expect(view).to_contain_text("5 days after the probation end date")
            await expect(view).to_contain_text("Farid Ismail")
            await expect(view.locator("input, select, textarea, button")).to_have_count(0)
            await page.wait_for_timeout(700)
            before = len(world.calls)
            await page.goto(f"{site.base}{NEW}")
            await expect(page.get_by_text("Your account can only view templates.")).to_be_visible(
                timeout=WAIT
            )
            await page.wait_for_timeout(700)
            assert [c for c in world.calls if c["method"] in {"POST", "PUT"}] == []
            # The notice is all the page shows: no list is read for it.
            lists = {"hr/people", "hr/templates", "hr/departments"}
            read = [c["path"] for c in world.calls[before:] if c["path"] in lists]
            assert read == [], read

    await with_browser(body)


async def test_staff_without_hr_duties_cannot_reach_the_editor(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        notice = lambda page: page.get_by_text(  # noqa: E731
            "You don't have access to this part of People & HR."
        )
        path = template_path(WAREHOUSE)
        async with processes_page(browser, site, world, path, caller=STAFF, ready=notice) as page:
            await expect(editor(page)).to_have_count(0)
            await page.goto(f"{site.base}{NEW}")
            await expect(notice(page)).to_be_visible(timeout=WAIT)
            touched = [c["path"] for c in world.calls if "templates" in c["path"]]
            assert touched == [], touched

    await with_browser(body)


async def test_keyboard_moves_keep_focus_on_the_moved_row(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        path = template_path(WAREHOUSE)
        async with processes_page(browser, site, world, path, ready=editor) as page:
            await expect(steps(page)).to_have_count(3, timeout=WAIT)
            down = step_row(page, 1).get_by_role("button", name="Move down")
            await expect(down).to_have_accessible_description(
                "Step 1: Sign the employment contract"
            )
            await down.focus()
            await page.keyboard.press("Enter")
            focused = page.locator(":focus")
            await expect(focused).to_have_accessible_name("Move down")
            await expect(focused).to_have_accessible_description(
                "Step 2: Sign the employment contract"
            )
            await page.keyboard.press("Space")
            # The last row cannot move down: focus stays on the row, on its other button.
            await expect(focused).to_have_accessible_name("Move up")
            await expect(focused).to_have_accessible_description(
                "Step 3: Sign the employment contract"
            )
            await page.keyboard.press("Enter")
            await expect(focused).to_have_accessible_description(
                "Step 2: Sign the employment contract"
            )
            assert await step_titles(page) == [
                "Set up the payroll record",
                "Sign the employment contract",
                "Meet your buddy",
            ]
            described = await editor(page).evaluate(
                "(root) => [...root.querySelectorAll('button[aria-describedby]')]"
                ".map((b) => b.getAttribute('aria-describedby'))"
            )
            assert described and all(described), described
            ids = await editor(page).evaluate(
                "(root) => [...root.querySelectorAll('legend[id]')].map((el) => el.id)"
            )
            assert len(ids) == len(set(ids)) == 3, ids

    await with_browser(body)


async def test_a_late_leaver_step_warns_after_saving(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        path = template_path(LEAVING)
        async with processes_page(browser, site, world, path, ready=editor) as page:
            await expect(steps(page)).to_have_count(1, timeout=WAIT)
            await fill_step(step_row(page, 1), due_offset_days="3")
            await editor(page).get_by_role("button", name="Save", exact=True).click()
            await expect(page.get_by_test_id("hr-template-warnings")).to_have_text(
                "Step 1, Return the laptop, is due after the last day. "
                "It will be due on the last day instead.",
                timeout=WAIT,
            )

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_templates_fit_phone_widths_in_three_themes(site: Site, theme: str) -> None:
    async def body(browser: Any) -> None:
        world = TemplatesWorld()
        async with processes_page(
            browser, site, world, TEMPLATES, theme=theme, width=320, ready=template_list
        ) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            await expect(page.get_by_test_id(f"hr-template-card-{WAREHOUSE}")).to_be_visible(
                timeout=WAIT
            )
            for where_path in (TEMPLATES, template_path(WAREHOUSE), NEW):
                if where_path != TEMPLATES:
                    await page.goto(f"{site.base}{where_path}")
                    await expect(steps(page).first).to_be_visible(timeout=WAIT)
                for width in (320, 390, 768):
                    await page.set_viewport_size({"width": width, "height": 900})
                    await page.wait_for_timeout(150)
                    where = (theme, where_path, width)
                    measured = await overflow(page)
                    assert measured["scroll"] <= measured["inner"], (where, measured)
                    if where_path == TEMPLATES:
                        table = width >= 768
                        await expect(page.get_by_test_id("hr-template-table")).to_be_visible(
                            visible=table
                        )
                        await expect(page.get_by_test_id("hr-template-cards")).to_be_visible(
                            visible=not table
                        )
                    sideways = await page.get_by_test_id("hr-processes").evaluate(SIDEWAYS)
                    assert sideways == {"out": [], "scrollers": []}, (where, sideways)

    await with_browser(body)


# ---- Create the default templates (HR PR 4) ----

DEFAULTS = "Create the default templates"
DEFAULTS_PATH = "hr/templates/defaults"
ONBOARDING_DEFAULT, OFFBOARDING_DEFAULT = (template.name for template in DEFAULT_TEMPLATES)
DEFAULTS_UNCERTAIN = (
    "We couldn't confirm that. Choose Create the default templates again; it won't be done twice."
)
DEFAULTS_UNAVAILABLE = "The default templates are not available right now."
NOTHING_CREATED = "These templates already exist: nothing was created."


def defaults_button(page: Page) -> Locator:
    return template_list(page).get_by_role("button", name=DEFAULTS, exact=True)


def defaults_dialog(page: Page) -> Locator:
    return page.get_by_test_id("hr-defaults-dialog")


def defaults_confirm(page: Page) -> Locator:
    return defaults_dialog(page).get_by_role("button", name=DEFAULTS, exact=True)


def defaults_notice(page: Page) -> Locator:
    return page.get_by_test_id("hr-defaults-notice")


def defaults_world(*, on: bool = True, empty: bool = True) -> TemplatesWorld:
    """The templates company with the deploy switch on and, by default, no template at all."""
    world = TemplatesWorld()
    world.processes.defaults_enabled = on
    if empty:
        world.processes.templates = []
    return world


async def test_the_create_defaults_button_never_exists_when_it_does_not_apply(site: Site) -> None:
    async def body(browser: Any) -> None:
        cases = {
            # The deploy switch is off: no button and no hint, with templates and without.
            "off with templates": defaults_world(on=False, empty=False),
            "off and empty": defaults_world(on=False),
            # The switch is on, but the company already has an active template of each kind.
            "on and created": defaults_world(on=True, empty=False),
        }
        for name, world in cases.items():
            async with processes_page(browser, site, world, TEMPLATES, ready=template_list) as page:
                await expect(
                    template_list(page).get_by_role("link", name="New template")
                ).to_be_visible(timeout=WAIT)
                await page.wait_for_timeout(300)
                await expect(template_list(page).get_by_role("button")).to_have_count(0)
                text = await template_list(page).inner_text()
                assert "default" not in text.lower(), (name, text)
                assert world.processes.defaults_calls == [], name
        # A read-only session never sees it, even with the switch on and nothing created.
        world = defaults_world()
        async with processes_page(
            browser, site, world, TEMPLATES, role="read_only", ready=template_list
        ) as page:
            await expect(template_list(page).get_by_text("No templates yet.")).to_be_visible(
                timeout=WAIT
            )
            await page.wait_for_timeout(300)
            await expect(template_list(page).get_by_role("button")).to_have_count(0)
            await expect(template_list(page)).not_to_contain_text("default")
            assert world.processes.defaults_calls == []
            assert [c for c in world.calls if c["method"] == "POST"] == []

    await with_browser(body)


async def test_the_defaults_dialog_says_what_it_creates_and_closes_cleanly(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = defaults_world()
        async with processes_page(browser, site, world, TEMPLATES, ready=template_list) as page:
            button = defaults_button(page)
            await expect(button).to_be_visible(timeout=WAIT)
            dialog = defaults_dialog(page)
            for closer in ("escape", "cancel", "close"):
                await button.click()
                await expect(dialog).to_be_visible()
                await expect(dialog.get_by_role("heading", name=DEFAULTS)).to_be_visible()
                assert await focus_place(page, "hr-defaults-dialog") == "dialog", closer
                for text in (
                    ONBOARDING_DEFAULT,
                    OFFBOARDING_DEFAULT,
                    "checklist of reminders",
                    "do not file anything",
                    "not legal advice",
                    "official source",
                    "company default",
                    "the next person you add starts onboarding with real tasks and reminders",
                    "You can edit both afterwards",
                    "Open either one from the template list to see all its steps",
                ):
                    await expect(dialog).to_contain_text(text)
                # aria-describedby points at one element that says what the dialog does.
                described = await dialog.get_attribute("aria-describedby")
                assert described, closer
                assert await page.locator(f"[id='{described}']").count() == 1, described
                await expect(page.locator(f"[id='{described}']")).to_contain_text(
                    "checklist of reminders"
                )
                text = await dialog.inner_text()
                assert not UUID.search(text), text
                assert not [code for code in CODES if code in text], text
                if closer == "escape":
                    await page.keyboard.press("Escape")
                elif closer == "cancel":
                    await dialog.get_by_role("button", name="Cancel", exact=True).click()
                else:
                    await dialog.get_by_role("button", name="Close", exact=True).click()
                await expect(dialog).to_have_count(0)
                await expect(button).to_be_focused()
            assert world.processes.defaults_calls == []
            assert world.template_calls("POST", DEFAULTS_PATH) == []

    await with_browser(body)


async def test_confirming_creates_both_templates_once_and_links_them(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = defaults_world()
        async with processes_page(browser, site, world, TEMPLATES, ready=template_list) as page:
            await defaults_button(page).click(timeout=WAIT)
            confirm = defaults_confirm(page)
            # Two clicks in the same task: the second must not send a second request.
            await confirm.evaluate("(button) => { button.click(); button.click(); }")
            notice = defaults_notice(page)
            await expect(notice).to_be_visible(timeout=WAIT)
            await expect(defaults_dialog(page)).to_have_count(0)
            await expect(notice).to_contain_text(
                f"Created {ONBOARDING_DEFAULT} and {OFFBOARDING_DEFAULT}."
            )
            await expect(notice).to_be_focused()
            assert await focus_place(page, "hr-defaults-notice") == "dialog"
            await page.wait_for_timeout(500)
            (call,) = world.template_calls("POST", DEFAULTS_PATH)
            assert_template_write(call, "POST", DEFAULTS_PATH, None)
            assert world.processes.defaults_calls == [OWNER]
            made = {t.name: t.template_id for t in world.processes.templates}
            assert set(made) == {ONBOARDING_DEFAULT, OFFBOARDING_DEFAULT}, made
            for name in made:
                link = notice.get_by_role("link", name=name, exact=True)
                await expect(link).to_have_attribute("href", template_path(made[name]))
            # The list was read again and shows both, with their steps; the button is gone.
            for template in DEFAULT_TEMPLATES:
                row = page.get_by_test_id(f"hr-template-row-{made[template.name]}")
                await expect(row).to_contain_text(f"{len(template.steps)} steps", timeout=WAIT)
                await expect(row).to_contain_text("Everyone")
            await expect(defaults_button(page)).to_have_count(0)
            text = await notice.inner_text()
            assert not UUID.search(text), text
            await notice.get_by_role("link", name=ONBOARDING_DEFAULT, exact=True).click()
            await page.wait_for_url(f"{site.base}{template_path(made[ONBOARDING_DEFAULT])}")
            await expect(editor(page).get_by_label("Name", exact=True)).to_have_value(
                ONBOARDING_DEFAULT, timeout=WAIT
            )

    await with_browser(body)


async def test_templates_that_already_exist_are_said_plainly_and_not_duplicated(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = defaults_world()
        everything = list(TemplatesWorld().processes.templates)
        async with processes_page(browser, site, world, TEMPLATES, ready=template_list) as page:
            await defaults_button(page).click(timeout=WAIT)
            # Another tab made them since this list was read.
            world.processes.templates = list(everything)
            await defaults_confirm(page).click()
            notice = defaults_notice(page)
            await expect(notice).to_have_text(NOTHING_CREATED, timeout=WAIT)
            await expect(notice).to_be_focused()
            assert [t.name for t in world.processes.templates] == [t.name for t in everything]
            assert world.processes.defaults_calls == [OWNER]
            # The list was read again: it shows what exists and no button.
            await expect(page.get_by_test_id(f"hr-template-row-{WAREHOUSE}")).to_be_visible(
                timeout=WAIT
            )
            await expect(defaults_button(page)).to_have_count(0)

    await with_browser(body)


async def test_one_kind_already_there_is_left_alone_and_the_other_is_created(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = defaults_world()
        everything = list(TemplatesWorld().processes.templates)
        async with processes_page(browser, site, world, TEMPLATES, ready=template_list) as page:
            await defaults_button(page).click(timeout=WAIT)
            world.processes.templates = [t for t in everything if t.kind != "offboarding"]
            await defaults_confirm(page).click()
            notice = defaults_notice(page)
            await expect(notice).to_contain_text(f"Created {OFFBOARDING_DEFAULT}.", timeout=WAIT)
            await expect(notice).to_contain_text(
                "The company already had an active onboarding template, so it was left as it is."
            )
            await expect(notice).not_to_contain_text(NOTHING_CREATED)
            assert [t.name for t in world.processes.templates if t.kind == "offboarding"] == [
                OFFBOARDING_DEFAULT
            ]

    await with_browser(body)


async def test_a_failed_create_keeps_the_dialog_open_and_a_retry_keeps_the_key(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = defaults_world()
        world.processes.failures[f"POST {DEFAULTS_PATH}"] = [(500, "synthetic_outage")]
        async with processes_page(browser, site, world, TEMPLATES, ready=template_list) as page:
            await defaults_button(page).click(timeout=WAIT)
            dialog = defaults_dialog(page)
            await defaults_confirm(page).click()
            await expect(dialog).to_contain_text(DEFAULTS_UNCERTAIN, timeout=WAIT)
            await expect(dialog.locator("[tabindex='-1']:has([role=alert])")).to_be_focused()
            assert await focus_place(page, "hr-defaults-dialog") == "dialog"
            await expect(defaults_notice(page)).to_have_count(0)
            # The confirm button works again, and the retry is the same operation (same key).
            await defaults_confirm(page).click()
            await expect(defaults_notice(page)).to_contain_text("Created", timeout=WAIT)
            await expect(dialog).to_have_count(0)
            calls = world.template_calls("POST", DEFAULTS_PATH)
            assert len(calls) == 2 and len({c["key"] for c in calls}) == 1 and calls[0]["key"], (
                calls
            )
            assert world.processes.defaults_calls == [OWNER]

    await with_browser(body)


async def test_the_switch_turned_off_mid_session_is_said_and_the_button_goes(site: Site) -> None:
    async def body(browser: Any) -> None:
        world = defaults_world()
        async with processes_page(browser, site, world, TEMPLATES, ready=template_list) as page:
            await defaults_button(page).click(timeout=WAIT)
            world.processes.defaults_enabled = False
            dialog = defaults_dialog(page)
            await defaults_confirm(page).click()
            await expect(dialog).to_contain_text(DEFAULTS_UNAVAILABLE, timeout=WAIT)
            await expect(dialog.locator("[tabindex='-1']:has([role=alert])")).to_be_focused()
            assert world.processes.defaults_calls == []
            await dialog.get_by_role("button", name="Cancel", exact=True).click()
            await expect(dialog).to_have_count(0)
            # The list was read again: the switch is off, so the button is gone, and focus did
            # not fall to the page.
            await expect(defaults_button(page)).to_have_count(0, timeout=WAIT)
            await expect(
                template_list(page).get_by_role("link", name="New template")
            ).to_be_focused()

    await with_browser(body)


@pytest.mark.parametrize("theme", THEMES)
async def test_the_defaults_dialog_and_notice_fit_phone_widths_in_three_themes(
    site: Site, theme: str
) -> None:
    async def body(browser: Any) -> None:
        world = defaults_world()
        async with processes_page(
            browser, site, world, TEMPLATES, theme=theme, width=320, ready=template_list
        ) as page:
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            await expect(defaults_button(page)).to_be_visible(timeout=WAIT)
            dialog = defaults_dialog(page)
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 640})
                await defaults_button(page).click()
                await expect(dialog).to_be_visible()
                await page.wait_for_timeout(150)
                where = (theme, width)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (where, measured)
                box = await dialog.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (where, box)
                sideways = await dialog.evaluate(SIDEWAYS)
                assert sideways == {"out": [], "scrollers": []}, (where, sideways)
                await page.keyboard.press("Escape")
                await expect(dialog).to_have_count(0)
            await page.set_viewport_size({"width": 320, "height": 900})
            await defaults_button(page).click()
            await defaults_confirm(page).click()
            await expect(defaults_notice(page)).to_be_visible(timeout=WAIT)
            for width in WIDTHS:
                await page.set_viewport_size({"width": width, "height": 900})
                await page.wait_for_timeout(150)
                measured = await overflow(page)
                assert measured["scroll"] <= measured["inner"], (theme, width, measured)
                sideways = await page.get_by_test_id("hr-processes").evaluate(SIDEWAYS)
                assert sideways == {"out": [], "scrollers": []}, (theme, width, sideways)

    await with_browser(body)
