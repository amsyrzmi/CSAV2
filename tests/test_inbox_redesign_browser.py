"""Real browser checks for the redesigned three-pane customer inbox.

Same stubbed-upstream harness as the services and WhatsApp browser tests: the real Next.js
shell runs from a disposable staged copy, server-side layout fetches hit a loopback stub,
and every browser-side `/console/v1/*` call is answered by Playwright from synthetic rows.
One shell serves the whole module; each test opens its own browser.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from csa.console.inbox_settings import InboxSettingsIn
from csa.inbox_queue.settings import InboxSettings
from playwright.async_api import Page, async_playwright, expect
from pydantic import ValidationError

from tests.test_payments_browser import synthetic_png
from tests.test_services_browser import (
    ROOT,
    TENANT,
    WAIT,
    WEB,
    Upstream,
    free_port,
    make_me,
    make_service,
)

pytestmark = pytest.mark.asyncio

NAMED = "a0000000-0000-4000-8000-000000000001"
UNNAMED = "a0000000-0000-4000-8000-000000000002"
NEWCOMER = "a0000000-0000-4000-8000-000000000003"
LONG = "a0000000-0000-4000-8000-000000000004"
LONG_NAME = ("Wolfeschlegelsteinhausenbergerdorff" * 2)[:60]
FULL_PHONE = "+60125550148"
MASKED_PHONE = "••••0148"
WIDTHS = (320, 390, 768, 1024, 1440)
# The signed-in person's id, as make_me returns it; the stub's "me" for `assignee=me` and `mine`.
ME_ID = "22222222-2222-4222-8222-222222222222"
PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
# Every key a saved view's `filters` has when the server sends it back (its model's defaults).
VIEW_DEFAULTS: dict[str, Any] = {
    "state": [],
    "assignee": None,
    "priority_min": None,
    "reason": [],
    "topic": [],
    "overdue": False,
    "waiting_over": None,
    "channel": None,
    "sender": None,
    "order": None,
}
QUEUE_FIELDS = {
    "queue_status",
    "queue_reason",
    "topic",
    "priority",
    "assignee_id",
    "assignee",
    "claimed_at",
    "needs_staff_since",
    "due_at",
    "snoozed_until",
    "last_customer_at",
    "last_staff_reply_at",
}


def ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def saved_view(name: str, filters: dict[str, Any], minutes: int = 0) -> dict[str, Any]:
    """A saved view as stored: `filters` holds only what is set."""
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "filters": filters,
        "created_at": ago(minutes),
    }


def conversation(
    identifier: str,
    channel: str,
    *,
    name: str | None,
    username: str | None = None,
    masked: str | None = None,
    known: bool = False,
    preview: str,
    minutes: int,
    pending: int = 0,
    human_muted: bool = False,
    queue_status: str = "bot",
    queue_reason: str | None = None,
    topic: str | None = None,
    priority: str = "normal",
    assignee_id: str | None = None,
    assignee_name: str | None = None,
    needs_staff_since: str | None = None,
    due_at: str | None = None,
    snoozed_until: str | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "channel": channel,
        "status": "open",
        "owned_by": "human" if human_muted else None,
        "ai_muted_until": None,
        "human_muted": human_muted,
        "inbox_version": 1,
        "opened_at": ago(minutes + 60),
        "contact": {
            "display_name": name,
            "telegram_username": username,
            "phone_masked": masked,
            "phone": None,
            "kind": "customer",
            "known": known,
        },
        "last_message": {"preview": preview, "at": ago(minutes)},
        "pending_approvals": pending,
        # The work-queue fields; a plain row is one the assistant has (`bot`), outside the queue.
        "queue_status": queue_status,
        "queue_reason": queue_reason,
        "topic": topic,
        "priority": priority,
        "assignee_id": assignee_id,
        "assignee": None
        if assignee_id is None
        else {"id": assignee_id, "display_name": assignee_name},
        "claimed_at": None if assignee_id is None else ago(minutes),
        "needs_staff_since": needs_staff_since,
        "due_at": due_at,
        "snoozed_until": snoozed_until,
        "last_customer_at": ago(minutes),
        "last_staff_reply_at": None,
    }


def message(
    identifier: str,
    direction: str,
    author: str,
    body: str,
    minutes: int,
    picture: str | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "direction": direction,
        "author": author,
        "body": body,
        "created_at": ago(minutes),
        "trace_id": None,
        "delivery_status": "delivered" if direction == "outbound" else None,
        "delivery_error": None,
        "provider_status": None,
        "provider_error": None,
        "picture": None if picture is None else {"asset_id": picture},
    }


@dataclass
class Inbox:
    """The synthetic inbox API one test sees; tests change it while the page polls."""

    items: list[dict[str, Any]] = field(
        default_factory=lambda: [
            conversation(
                NAMED,
                "telegram",
                name="Aisyah Rahman",
                username="aisyah_r",
                known=True,
                preview="Is my order on the way?",
                minutes=2,
                pending=1,
            ),
            # WhatsApp stored the number as the name, so the API sends no display name.
            conversation(
                UNNAMED,
                "whatsapp",
                name=None,
                masked=MASKED_PHONE,
                preview="Hello, do you deliver to Ipoh?",
                minutes=5,
            ),
            conversation(
                NEWCOMER,
                "telegram",
                name="Ben Tan",
                preview="First time asking here.",
                minutes=9,
                pending=1,
                human_muted=True,
            ),
            conversation(
                LONG,
                "whatsapp",
                name=LONG_NAME,
                masked="••••7781",
                known=True,
                preview="A long name should never break the row.",
                minutes=30,
            ),
        ]
    )
    needs_review_count: int = 2
    # The tab counts the list sends: computed from the rows unless a test fixes them, or null.
    counts_override: dict[str, Any] | None = None
    null_counts: bool = False
    # Off by default: the older tests expect every row whatever tab the page opens on.
    filter_state: bool = False
    # The people the Filters panel offers, and what the assignee read answers (200 or a status).
    assignees: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "id": "33333333-3333-4333-8333-333333333331",
                "display_name": "Aina Demo",
                "role": "tenant_staff",
            },
            {"id": ME_ID, "display_name": "Synthetic User", "role": "tenant_admin"},
        ]
    )
    assignees_status: int = 200
    assignee_calls: int = 0
    fail_lists: bool = False
    # A 403 `detail` code for the list (for example "package_inactive"), or None.
    refuse_lists: str | None = None
    list_queries: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Held replies the approvals read returns for a conversation, and what decide answered.
    approvals: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    decide_result: dict[str, Any] = field(default_factory=dict)
    # Bodies of the replies staff queued, and how long the stub takes to answer one.
    replies: list[str] = field(default_factory=list)
    reply_delay: float = 0.0
    # Off by default: the older tests type into the search box without caring about the rows.
    filter_search: bool = False
    # The assistant's handoff note per conversation; only the single-conversation read has it.
    notes: dict[str, dict[str, str]] = field(default_factory=dict)
    # What the picture route answers (200 = a synthetic PNG, else that status) and was asked.
    picture_status: int = 200
    picture_requests: list[str] = field(default_factory=list)
    # Queue actions the page sent ({"id", "action", "body", "query", "at"}). Answers are scripted
    # per action and used in order: (status, json) or "abort" (no answer at all); with none left the
    # stub applies the action to the row. `queue_delay` holds every answer back that long.
    queue_calls: list[dict[str, Any]] = field(default_factory=list)
    queue_script: dict[str, list[Any]] = field(default_factory=dict)
    queue_delay: float = 0.0
    # Takeover and release calls ({"id", "action", "body"}), and when each conversation was read.
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    detail_reads: list[float] = field(default_factory=list)
    # Rows sent as an older server sent them, without any queue field (list and detail).
    bare: set[str] = field(default_factory=set)
    # Bulk requests the page sent ({"body", "query"}). Answers are scripted in order: (status, json)
    # or "abort"; with none left each id is applied as one action (`done`), unless `bulk_outcomes`
    # names its outcome, and an id that is not a row is `not_found`. `as_of` fixes the list's read
    # time (otherwise it is the time of each read); `omit_as_of` answers without one, as an older
    # server does.
    bulk_calls: list[dict[str, Any]] = field(default_factory=list)
    bulk_script: list[Any] = field(default_factory=list)
    bulk_outcomes: dict[str, str] = field(default_factory=dict)
    as_of: str | None = None
    omit_as_of: bool = False
    # Saved views per person (user id -> views, newest first; `filters` as stored, only what is
    # set), the signed-in person (`inbox_page` signs this id in), the view calls the page made
    # ({"method", "path", "body", "query"}), scripted answers per method used in order ((status,
    # json)), and gates per method: while a test holds one shut, that answer waits until it is set.
    views: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    viewer: str = ME_ID
    view_calls: list[dict[str, Any]] = field(default_factory=list)
    views_script: dict[str, list[Any]] = field(default_factory=dict)
    views_gates: dict[str, asyncio.Event] = field(default_factory=dict)
    # The company's Inbox settings as the server stores them (the defaults with `version` 0 until a
    # first save), the settings calls the page made ({"method", "body", "query"}), scripted answers
    # per method used in order ((status, json)), and gates per method: while a test holds one shut,
    # that answer waits until the test sets it. `role` is the signed-in person's (`inbox_page` sets
    # it): tenant staff may read the settings but a save is 403, as on the server.
    settings: dict[str, Any] = field(
        default_factory=lambda: {**InboxSettings().model_dump(), "version": 0}
    )
    settings_calls: list[dict[str, Any]] = field(default_factory=list)
    settings_script: dict[str, list[Any]] = field(default_factory=dict)
    settings_gates: dict[str, asyncio.Event] = field(default_factory=dict)
    role: str = "tenant_admin"
    # The companies a platform administrator picks from.
    clients: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": TENANT, "slug": "synthetic", "name": "Synthetic Tenant", "status": "active"}
        ]
    )

    def queue_counts(self) -> dict[str, Any] | None:
        """The tab counts, from the rows (as the server counts them) unless a test set them."""
        if self.null_counts:
            return None
        if self.counts_override is not None:
            return self.counts_override
        now = datetime.now(UTC).isoformat()

        def count(state: str, extra: Any = lambda _item: True) -> int:
            return sum(1 for item in self.items if item["queue_status"] == state and extra(item))

        waiting = [
            item["needs_staff_since"]
            for item in self.items
            if item["queue_status"] == "needs_staff" and item["needs_staff_since"]
        ]
        return {
            "needs_us": count("needs_staff"),
            "mine": count("needs_staff", lambda i: i["assignee_id"] == ME_ID)
            + count("waiting_customer", lambda i: i["assignee_id"] == ME_ID),
            "unassigned": count("needs_staff", lambda i: i["assignee_id"] is None),
            "overdue": count("needs_staff", lambda i: bool(i["due_at"]) and i["due_at"] < now),
            "waiting_customer": count("waiting_customer"),
            "snoozed": count("snoozed"),
            "oldest_waiting_at": min(waiting) if waiting else None,
        }

    def queue_filter(self, items: list[dict[str, Any]], params: dict[str, list[str]]) -> list[Any]:
        """The queue filters, close enough to the server's for the screen's tests."""
        if self.filter_state and "state" in params:
            items = [item for item in items if item["queue_status"] in params["state"]]
        if "assignee" in params:
            wanted = params["assignee"][0]
            target = ME_ID if wanted == "me" else None if wanted == "none" else wanted
            items = [item for item in items if item["assignee_id"] == target]
        if "priority_min" in params:
            floor = PRIORITY_RANK[params["priority_min"][0]]
            items = [item for item in items if PRIORITY_RANK[item["priority"]] >= floor]
        if "reason" in params:
            items = [item for item in items if item["queue_reason"] in params["reason"]]
        if "topic" in params:
            items = [item for item in items if item["topic"] in params["topic"]]
        if params.get("overdue") == ["true"]:
            now = datetime.now(UTC).isoformat()
            items = [item for item in items if item["due_at"] and item["due_at"] < now]
        return items

    def thread(self, identifier: str) -> list[dict[str, Any]]:
        """Newest first, as the API pages them."""
        if identifier not in self.messages:
            self.messages[identifier] = [
                message(f"{identifier[:-3]}m02", "outbound", "ai", "Let me check that.", 20),
                message(f"{identifier[:-3]}m01", "inbound", "customer", "Hi there.", 21),
            ]
        return self.messages[identifier]

    def detail(self, identifier: str) -> dict[str, Any] | None:
        row = next((item for item in self.items if item["id"] == identifier), None)
        if row is None:
            return None
        # Only the single-conversation read carries the full number.
        phone = FULL_PHONE if identifier == UNNAMED else None
        note = self.notes.get(identifier)
        extra = {"handoff_note": note} if note else {}
        return self.served({**row, "contact": {**row["contact"], "phone": phone}, **extra})

    def served(self, item: dict[str, Any]) -> dict[str, Any]:
        """A row as sent: without its queue fields when the test plays an older server."""
        if item["id"] not in self.bare:
            return item
        return {key: value for key, value in item.items() if key not in QUEUE_FIELDS}

    def apply_queue(self, identifier: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        """A queue action as the server applies it, close enough for the screen's tests."""
        row = next(item for item in self.items if item["id"] == identifier)
        cleared = {
            "queue_reason": None,
            "assignee_id": None,
            "assignee": None,
            "needs_staff_since": None,
            "due_at": None,
            "snoozed_until": None,
        }
        if action == "claim":
            row.update(assignee_id=ME_ID, assignee={"id": ME_ID, "display_name": "Synthetic User"})
        elif action == "unclaim":
            row.update(assignee_id=None, assignee=None)
        elif action == "assign":
            person = next((p for p in self.assignees if p["id"] == body["assignee_id"]), None)
            name = person["display_name"] if person else None
            row.update(
                assignee_id=body["assignee_id"],
                assignee={"id": body["assignee_id"], "display_name": name},
            )
        elif action == "resolve":
            row.update(queue_status="resolved", **cleared)
        elif action == "snooze":
            claim = {"assignee_id": row["assignee_id"], "assignee": row["assignee"]}
            row.update(
                queue_status="snoozed", **{**cleared, **claim, "snoozed_until": body["until"]}
            )
        elif action in ("priority", "topic"):
            row[action] = body[action]
        row["inbox_version"] += 1
        return {"outcome": "applied", "conversation": self.detail(identifier)}

    async def views_route(self, request: Any, path: str, query: str) -> tuple[int, Any]:
        """The saved views as the server keeps them: per person, unique names, 20 at most."""
        method = request.method
        body = request.post_data_json if method in ("POST", "PUT") else None
        self.view_calls.append({"method": method, "path": path, "body": body, "query": query})
        gate = self.views_gates.get(method)
        if gate is not None:
            await gate.wait()
        script = self.views_script.get(method) or []
        if script:
            return script.pop(0)
        mine = self.views.setdefault(self.viewer, [])

        def served(view: dict[str, Any]) -> dict[str, Any]:
            return {**view, "filters": {**VIEW_DEFAULTS, **view["filters"]}}

        def taken(name: str, other_than: str | None = None) -> bool:
            return any(v["name"].lower() == name.lower() and v["id"] != other_than for v in mine)

        if method == "GET":
            return 200, [served(view) for view in mine]
        if method == "POST":
            name = body["name"].strip()
            if taken(name):
                return 409, {"detail": "you already have a saved view with this name"}
            if len(mine) >= 20:
                return 409, {"detail": "you can keep at most 20 saved views"}
            view = saved_view(name, body["filters"])
            mine.insert(0, view)
            return 201, served(view)
        target = next((view for view in mine if view["id"] == path.split("/")[-1]), None)
        if target is None:
            return 404, {"detail": "saved view not found"}
        if method == "PUT":
            if "name" in body:
                if taken(body["name"].strip(), target["id"]):
                    return 409, {"detail": "you already have a saved view with this name"}
                target["name"] = body["name"].strip()
            if "filters" in body:
                target["filters"] = body["filters"]
            return 200, served(target)
        mine.remove(target)
        return 204, None

    async def settings_route(self, request: Any, query: str) -> tuple[int, Any]:
        """The company's settings as the API keeps them: every field, ranges, version, admins."""
        method = request.method
        body = request.post_data_json if method == "PUT" else None
        self.settings_calls.append({"method": method, "body": body, "query": query})
        gate = self.settings_gates.get(method)
        if gate is not None:
            await gate.wait()
        script = self.settings_script.get(method) or []
        if script:
            return script.pop(0)
        if method == "GET":
            return 200, dict(self.settings)
        if self.role == "tenant_staff":
            return 403, {"detail": "forbidden"}
        try:
            saved = InboxSettingsIn.model_validate(body)
        except ValidationError:
            return 422, {"detail": [{"msg": "synthetic validation error"}]}
        if saved.version != self.settings["version"]:
            return 409, {"detail": "version_conflict"}
        self.settings = {**saved.model_dump(), "version": saved.version + 1}
        return 200, dict(self.settings)

    async def route(self, request_route: Any) -> None:
        request = request_route.request
        path, _, query = request.url.split("/console/v1/")[1].partition("?")
        status, data = 200, None
        if path == "auth/csrf":
            data = {"csrf_token": "synthetic-csrf"}
        elif path == "services/customer-replies":
            data = {"mode": "auto", "brand_name": "Synthetic", "can_manage": True, "warnings": []}
        elif path == "services" and request.method == "GET":
            data = [make_service("customer_support", "Customer Support", "base", "active")]
        elif path == "inbox/conversations":
            self.list_queries.append(query)
            params = parse_qs(query)
            items = self.items
            if params.get("needs_review") == ["true"]:
                items = [item for item in items if item["pending_approvals"] > 0]
            items = self.queue_filter(items, params)
            if "channel" in params:
                items = [item for item in items if item["channel"] in params["channel"]]
            if "sender" in params:
                known = params["sender"] == ["known"]
                items = [item for item in items if item["contact"]["known"] == known]
            if self.filter_search and "q" in params:
                needle = params["q"][0].lower()
                items = [
                    item
                    for item in items
                    if needle
                    in " ".join(
                        str(item["contact"][key] or "")
                        for key in ("display_name", "telegram_username", "phone_masked")
                    ).lower()
                ]
            if self.refuse_lists is not None:
                status, data = 403, {"detail": self.refuse_lists}
            elif self.fail_lists:
                status, data = 500, {"detail": "synthetic outage"}
            else:
                data = {
                    "items": [self.served(item) for item in items],
                    "next_cursor": None,
                    "needs_review_count": self.needs_review_count,
                    "counts": self.queue_counts(),
                    "as_of": self.as_of or datetime.now(UTC).isoformat(),
                }
                if self.omit_as_of:
                    del data["as_of"]
        elif path == "inbox/assignees" and request.method == "GET":
            self.assignee_calls += 1
            if self.assignees_status == 200:
                data = self.assignees
            else:
                status, data = self.assignees_status, {"detail": "synthetic directory outage"}
        elif path == "admin/clients" and request.method == "GET":
            data = self.clients
        elif path == "inbox/settings" and request.method in ("GET", "PUT"):
            status, data = await self.settings_route(request, query)
        elif path == "inbox/views" or path.startswith("inbox/views/"):
            status, data = await self.views_route(request, path, query)
            if status == 204:
                await request_route.fulfill(status=204, body="")
                return
        elif path.startswith("inbox/conversations/") and "/queue/" in path:
            identifier, action = path.split("/")[2], path.split("/")[4]
            body = request.post_data_json or {}
            self.queue_calls.append(
                {
                    "id": identifier,
                    "action": action,
                    "body": body,
                    "query": query,
                    "at": time.monotonic(),
                }
            )
            await asyncio.sleep(self.queue_delay)
            script = self.queue_script.get(action) or []
            answer = script.pop(0) if script else None
            if answer == "abort":
                await request_route.abort("failed")
                return
            if answer is None:
                data = self.apply_queue(identifier, action, body)
            else:
                status, data = answer
        elif path == "inbox/bulk" and request.method == "POST":
            body = request.post_data_json or {}
            self.bulk_calls.append({"body": body, "query": query})
            answer = self.bulk_script.pop(0) if self.bulk_script else None
            if answer == "abort":
                await request_route.abort("failed")
                return
            if answer is not None:
                status, data = answer
            else:
                results = []
                for identifier in body.get("ids", []):
                    outcome = self.bulk_outcomes.get(identifier)
                    if outcome is None and any(item["id"] == identifier for item in self.items):
                        self.apply_queue(identifier, body["action"], body)
                        outcome = "done"
                    results.append({"id": identifier, "outcome": outcome or "not_found"})
                data = {"results": results}
        elif path.startswith("inbox/conversations/") and path.endswith(("/takeover", "/release")):
            identifier, action = path.split("/")[2], path.split("/")[3]
            self.handoffs.append(
                {"id": identifier, "action": action, "body": request.post_data_json or {}}
            )
            taken = action == "takeover"
            for item in self.items:
                if item["id"] == identifier:
                    item.update(
                        human_muted=taken, owned_by="human" if taken else None, ai_muted_until=None
                    )
                    item["inbox_version"] = item.get("inbox_version", 1) + 1
            data = {"outcome": "applied", "queued": False, "warnings": [], "status": None}
        elif path.startswith("inbox/conversations/") and path.endswith("/reply"):
            await asyncio.sleep(self.reply_delay)
            self.replies.append((request.post_data_json or {}).get("body", ""))
            data = {"outcome": "applied", "queued": True, "warnings": [], "status": None}
            status = 202
        elif path.startswith("inbox/conversations/") and path.endswith("/messages"):
            data = {"items": self.thread(path.split("/")[2]), "next_cursor": None}
        elif path.startswith("inbox/conversations/"):
            self.detail_reads.append(time.monotonic())
            data = self.detail(path.split("/")[2])
            if data is None:
                status, data = 404, {"detail": "conversation not found"}
        elif path.startswith("media/"):
            self.picture_requests.append(path)
            if self.picture_status == 200:
                await request_route.fulfill(
                    status=200, content_type="image/png", body=synthetic_png(160)
                )
                return
            status, data = self.picture_status, {"detail": "picture_not_found"}
        elif path == "inbox/approvals":
            wanted = parse_qs(query).get("conversation_id", [])
            items = [item for item in self.approvals if item["conversation_id"] in wanted]
            data = {"items": items, "next_cursor": None}
        elif path.startswith("inbox/approvals/") and path.endswith("/decide"):
            self.decisions.append({"id": path.split("/")[2], **(request.post_data_json or {})})
            data = self.decide_result
        else:
            self.unexpected.append(f"{request.method} {path}?{query}")
            status, data = 500, {"detail": "unexpected synthetic call"}
        await request_route.fulfill(status=status, json=data)


@dataclass
class Shell:
    base: str
    upstream: Upstream
    logs: Path


@pytest.fixture(scope="module")
def shell(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Shell]:
    """One staged Next.js shell for the module; each `next dev` start costs a minute."""
    node = shutil.which("node")
    assert node, "Node is required for browser inbox acceptance"
    assert (WEB / "node_modules/next/dist/bin/next").is_file(), (
        "Console dependencies are missing; run `npm ci --ignore-scripts` in "
        "CSA_WebConsole/csa-console/web before browser inbox acceptance"
    )
    tmp_path = tmp_path_factory.mktemp("inbox-shell")
    stage = tmp_path / "web"
    with Upstream() as upstream:
        environment = {
            "PATH": os.environ["PATH"],
            "NEXT_TELEMETRY_DISABLED": "1",
            "CSA_CONSOLE_API_ORIGIN": upstream.origin,
        }
        subprocess.run(
            [node, str(WEB / "scripts/check-isolated.mjs"), "--context", str(stage)],
            check=True,
            cwd=ROOT,
            env=environment,
            capture_output=True,
        )
        (stage / "node_modules").symlink_to(WEB / "node_modules", target_is_directory=True)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        log_path = tmp_path / "frontend.log"
        log = log_path.open("w")
        process = subprocess.Popen(
            [
                node,
                str(WEB / "node_modules/next/dist/bin/next"),
                "dev",
                "--webpack",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=stage,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            upstream.me = make_me("tenant_admin")
            upstream.packages = [{"key": "customer_support", "status": "active"}]
            session = {"csa_session": "synthetic-session"}
            with httpx.Client(trust_env=False, cookies=session) as http:
                for _ in range(120):
                    assert process.poll() is None, log_path.read_text()
                    try:
                        if http.get(f"{base}/login", timeout=2).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.5)
                else:
                    pytest.fail(f"Synthetic inbox frontend did not start; log: {log_path}")
                # Compile the inbox route once, so the first test does not pay for it.
                http.get(f"{base}/portal/inbox", timeout=240)
            yield Shell(base=base, upstream=upstream, logs=tmp_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


@contextlib.asynccontextmanager
async def inbox_page(
    shell: Shell,
    state: Inbox,
    *,
    width: int = 1440,
    role: str = "tenant_admin",
    reduced_motion: bool = False,
    theme: str = "cobalt",
    path: str = "/portal/inbox",
) -> AsyncIterator[Page]:
    """A logged-in page on the inbox, answered by `state`; checks for stray calls on exit."""
    me = make_me(role)
    state.role = role
    # The signed-in person is the one whose saved views the stub serves.
    if state.viewer != ME_ID:
        me["user"] = {**me["user"], "id": state.viewer, "display_name": "Second Member"}
    shell.upstream.me = me
    shell.upstream.packages = [{"key": "customer_support", "status": "active"}]
    shell.upstream.unexpected.clear()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(
            viewport={"width": width, "height": 844 if width < 768 else 900},
            reduced_motion="reduce" if reduced_motion else "no-preference",
        )
        context.set_default_navigation_timeout(WAIT)
        await context.add_cookies(
            [
                {"name": "csa_session", "value": "synthetic-session", "url": shell.base},
                {"name": "fs-theme", "value": theme, "url": shell.base},
            ]
        )
        page = await context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.route("**/console/v1/**", state.route)
            await page.goto(f"{shell.base}{path}")
            yield page
            assert not errors, errors
            assert not state.unexpected, state.unexpected
            assert not shell.upstream.unexpected, shell.upstream.unexpected
        except Exception as exc:
            exc.add_note(f"Page errors: {errors}; frontend log: {shell.logs / 'frontend.log'}")
            raise
        finally:
            await context.close()
            await browser.close()


def row(page: Page, identifier: str) -> Any:
    return page.locator(f'[data-conversation-id="{identifier}"]')


async def rows_loaded(page: Page, count: int = 4) -> None:
    await expect(page.locator("[data-conversation-id]")).to_have_count(count, timeout=WAIT)


async def open_thread(page: Page, identifier: str, text: str = "Let me check that.") -> None:
    await row(page, identifier).click()
    await expect(page.get_by_test_id("inbox-thread").get_by_text(text, exact=True)).to_be_visible(
        timeout=WAIT
    )


async def selected_id(page: Page) -> str | None:
    return await page.locator('[data-conversation-id][aria-current="true"]').get_attribute(
        "data-conversation-id"
    )


async def eventually(check: Any, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "condition not met in time"
        await asyncio.sleep(0.1)


async def page_width(page: Page) -> dict[str, int]:
    return await page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})"
    )


async def test_no_page_overflow_at_all_widths(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state, width=WIDTHS[0]) as page:
        for width in WIDTHS:
            await page.set_viewport_size({"width": width, "height": 900})
            await page.goto(f"{shell.base}/portal/inbox")
            await rows_loaded(page)
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], ("list", width, measured)
            await open_thread(page, LONG)
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], ("thread", width, measured)


async def test_long_name_does_not_break_the_row(shell: Shell) -> None:
    assert len(LONG_NAME) == 60 and " " not in LONG_NAME
    state = Inbox()
    for width in (320, 1440):
        async with inbox_page(shell, state, width=width) as page:
            await rows_loaded(page)
            button = row(page, LONG)
            title = button.locator("span.truncate").first
            await expect(title).to_have_text(LONG_NAME)
            box = await button.bounding_box()
            container = await page.get_by_role("list", name="Conversations").bounding_box()
            assert box and container
            assert box["x"] >= container["x"] - 0.5, (width, box, container)
            assert box["x"] + box["width"] <= container["x"] + container["width"] + 0.5, (
                width,
                box,
                container,
            )
            clipped = await title.evaluate(
                """el => ({
                    ellipsis: getComputedStyle(el).textOverflow,
                    cut: el.scrollWidth > el.clientWidth,
                })"""
            )
            assert clipped == {"ellipsis": "ellipsis", "cut": True}, (width, clipped)


def held(state: Inbox) -> Inbox:
    """The two rows with a held reply wait on staff for it, as the queue model files them."""
    for item in state.items:
        if item["pending_approvals"] > 0:
            item.update(queue_status="needs_staff", queue_reason="held_reply")
            item["needs_staff_since"] = item["last_message"]["at"]
    return state


async def open_filters(page: Page) -> Any:
    await page.get_by_test_id("inbox-queue-filters").click()
    panel = page.get_by_role("dialog", name="Filters")
    await expect(panel).to_be_visible()
    return panel


async def test_needs_us_count_stays_visible_and_held_replies_filter_on_the_server(
    shell: Shell,
) -> None:
    # Was the "Needs review" count and chip: the count is now the Needs us tab's, and a held
    # reply is the Reason filter. The needs_review parameter is never sent any more.
    state = held(Inbox())
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        count = page.get_by_test_id("inbox-needs-us-count")
        await expect(count).to_have_text("2")
        await expect(count).to_be_visible()
        panel = await open_filters(page)
        await panel.get_by_role("checkbox", name="Held reply").check()
        await rows_loaded(page, 2)
        assert any("reason=held_reply" in query for query in state.list_queries)
        assert not any("needs_review" in query for query in state.list_queries)
        await expect(row(page, NAMED)).to_have_count(1)
        await expect(row(page, NEWCOMER)).to_have_count(1)
        await expect(count).to_be_visible()
        await expect(count).to_have_text("2")


async def test_search_is_sent_to_the_server_and_ignores_one_character(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        search = page.get_by_test_id("inbox-search")
        await search.fill("A")
        await expect(page.get_by_text("Type at least two characters to search.")).to_be_visible()
        # Past the 300 ms debounce and through at least one 3-second poll.
        await asyncio.sleep(3.5)
        assert not any("q=" in query for query in state.list_queries), state.list_queries
        await search.fill("Ai")
        await eventually(lambda: any("q=Ai" in query for query in state.list_queries))
        assert all(parse_qs(query).get("q", ["Ai"]) == ["Ai"] for query in state.list_queries), (
            state.list_queries
        )


async def test_failed_refresh_keeps_rows(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        calls = len(state.list_queries)
        state.fail_lists = True
        await expect(page.get_by_test_id("inbox-list-state-unavailable")).to_be_visible(
            timeout=WAIT
        )
        assert len(state.list_queries) > calls
        await expect(page.locator("[data-conversation-id]")).to_have_count(4)
        await expect(page.get_by_test_id("inbox-list-state-empty")).to_have_count(0)
        await expect(row(page, NAMED)).to_contain_text("Aisyah Rahman")


async def test_inactive_package_shows_the_servers_plain_words_and_role_403_shows_access_copy(
    shell: Shell,
) -> None:
    # The API answers 403 for two reasons; only the role refusal gets the fixed copy.
    package = "not part of your organisation's active service packages"
    no_access = (
        "You do not have access to the customer inbox. "
        "Ask your administrator to give you staff access."
    )
    async with inbox_page(shell, Inbox(refuse_lists="package_inactive")) as page:
        refused = page.get_by_test_id("inbox-list-state-forbidden")
        await expect(refused).to_be_visible(timeout=WAIT)
        await expect(refused.get_by_text(package)).to_be_visible()
        await expect(page.get_by_text(no_access, exact=True)).to_have_count(0)
        await expect(page.get_by_role("button", name="Try again")).to_have_count(0)
        await expect(page.locator("[data-conversation-id]")).to_have_count(0)
    async with inbox_page(shell, Inbox(refuse_lists="forbidden")) as page:
        refused = page.get_by_test_id("inbox-list-state-forbidden")
        await expect(refused).to_be_visible(timeout=WAIT)
        await expect(refused.get_by_text(no_access, exact=True)).to_be_visible()
        await expect(page.get_by_text(package)).to_have_count(0)
        await expect(page.get_by_role("button", name="Try again")).to_have_count(0)
        await expect(page.locator("[data-conversation-id]")).to_have_count(0)


async def test_unnamed_whatsapp_sender_shows_masked_number_in_list_and_full_in_card(
    shell: Shell,
) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        listing = page.get_by_role("list", name="Conversations")
        await expect(row(page, UNNAMED).locator("span.truncate").first).to_have_text(MASKED_PHONE)
        assert "0125550148" not in await listing.inner_text()
        await open_thread(page, UNNAMED)
        card = page.get_by_test_id("inbox-customer-card")
        await expect(card).to_contain_text(FULL_PHONE, timeout=WAIT)
        assert "0125550148" not in await listing.inner_text()
        # `inner_text` skips hidden and attribute text; the markup would show a leak there.
        markup = await listing.evaluate("el => el.outerHTML")
        assert MASKED_PHONE in markup
        assert "0125550148" not in markup
        # The full number belongs to the customer card; the thread itself never prints it.
        assert "0125550148" not in await page.get_by_test_id("inbox-thread").inner_text()


async def test_expired_approval_shows_approval_expired(shell: Shell) -> None:
    held = {
        "id": 7,
        "conversation_id": NAMED,
        "draft": "Your order ships tomorrow.",
        "reason": "Mentions a delivery date.",
        "status": "pending",
        "expires_at": ago(-30),
    }
    # The server found the approval past its expiry when staff decided: 200, nothing queued.
    state = Inbox(
        approvals=[held],
        decide_result={
            "outcome": "expired",
            "status": "expired",
            "queued": False,
            "warnings": [],
        },
    )
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        await open_thread(page, NAMED)
        review = page.get_by_role("article", name="Held reply, needs review")
        await expect(review).to_be_visible(timeout=WAIT)
        await review.get_by_role("button", name="Edit", exact=True).click()
        await review.get_by_label("Replacement reply").fill("Your order ships on Friday.")
        page.once("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))
        await review.get_by_role("button", name="Send edited reply").click()
        await expect(page.get_by_text("Approval expired. No reply queued.")).to_be_visible(
            timeout=WAIT
        )
        assert [(call["id"], call["decision"]) for call in state.decisions] == [("7", "edit")]
        # Not reported as a send or as handled by someone else.
        await expect(
            page.get_by_text(re.compile(r"^(Reply queued|Already handled)"))
        ).to_have_count(0)
        # The stub keeps listing the reply, so the page must not drop the card by itself.
        await expect(review).to_be_visible()


async def test_j_k_move_selection_but_not_inside_inputs(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        await open_thread(page, NAMED)
        assert await selected_id(page) == NAMED
        # Outside any field, j moves to the next row and k back.
        await page.keyboard.press("j")
        await expect(row(page, UNNAMED)).to_have_attribute("aria-current", "true")
        await page.keyboard.press("k")
        await expect(row(page, NAMED)).to_have_attribute("aria-current", "true")

        # Staff has taken over this one, so the human-reply composer is on screen.
        await open_thread(page, NEWCOMER)
        await expect(page.get_by_label("Human reply", exact=True)).to_be_visible(timeout=WAIT)
        # Held by tag: the wrapping label's text grows with the textarea's contents.
        reply = page.get_by_test_id("inbox-thread").locator("textarea")
        await reply.press_sequentially("jk")
        await expect(reply).to_have_value("jk")
        await asyncio.sleep(0.3)
        assert await selected_id(page) == NEWCOMER

        search = page.get_by_test_id("inbox-search")
        await search.press_sequentially("jk")
        await expect(search).to_have_value("jk")
        await asyncio.sleep(0.3)
        assert await selected_id(page) == NEWCOMER


async def test_slide_over_opens_closes_with_escape_and_returns_focus(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state, width=390) as page:
        await rows_loaded(page)
        await open_thread(page, UNNAMED)
        details = page.get_by_test_id("inbox-details-button")
        await details.click()
        dialog = page.get_by_role("dialog", name="Customer details")
        await expect(dialog).to_be_visible()
        await expect(dialog.get_by_test_id("inbox-customer-card")).to_contain_text(
            FULL_PHONE, timeout=WAIT
        )
        # While the panel is open, focus has moved into it and left the opener behind.
        await expect(details).not_to_be_focused()
        inside = await dialog.evaluate("el => el.contains(document.activeElement)")
        assert inside, "focus stayed outside the open dialog"
        await page.keyboard.press("Escape")
        await expect(dialog).to_be_hidden()
        await expect(details).to_be_focused()
        # Escape closed only the panel; the thread is still open.
        await expect(page.get_by_test_id("inbox-thread")).to_be_visible()


async def test_back_on_phone_returns_focus_to_the_open_conversation_row(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state, width=390) as page:
        await rows_loaded(page)
        await open_thread(page, UNNAMED)
        await expect(row(page, UNNAMED)).to_be_hidden()
        await page.get_by_role("button", name="Back to conversations").click()
        await expect(row(page, UNNAMED)).to_be_visible()
        await expect(row(page, UNNAMED)).to_be_focused()


NO_BOX_JS = """(args) => {
    const [selector, rootSelector] = args;
    const root = document.querySelector(rootSelector);
    const found = [...root.querySelectorAll(selector)];
    return found.map((el) => {
        const chain = [];
        for (let node = el; node && node !== root; node = node.parentElement) {
            const s = getComputedStyle(node);
            chain.push({
                tag: node.tagName.toLowerCase(),
                agent: node.dataset.agent || null,
                background: s.backgroundColor + " " + s.backgroundImage,
                radius: s.borderRadius,
                border: s.borderTopWidth,
                overflow: s.overflow,
            });
        }
        return chain;
    });
}"""


def assert_no_box(found: list[list[dict[str, str]]], expected: int) -> None:
    """Every avatar and every wrapper up to its container is transparent, square and unclipped."""
    assert len(found) == expected, found
    for chain in found:
        assert chain, found
        for box in chain:
            assert box["background"] == "rgba(0, 0, 0, 0) none", box
            assert box["radius"] == "0px" and box["border"] == "0px", box
            assert box["overflow"] == "visible", box


async def test_empty_state_connect_button_only_for_admins(shell: Shell) -> None:
    ask = "Ask your administrator to connect WhatsApp."
    async with inbox_page(shell, Inbox(items=[], needs_review_count=0)) as page:
        empty = page.get_by_test_id("inbox-list-state-empty")
        await expect(empty).to_be_visible(timeout=WAIT)
        await expect(empty.get_by_role("heading", name="No conversations yet")).to_be_visible()
        # The empty-state avatar is drawn straight onto the page: no box of any kind.
        found = await page.evaluate(
            NO_BOX_JS, ["[data-agent]", '[data-testid="inbox-list-state-empty"]']
        )
        assert_no_box(found, 1)
        connect = page.get_by_test_id("inbox-connect-whatsapp")
        await expect(connect).to_be_visible()
        await expect(connect).to_have_attribute("href", "/portal/services")
        await expect(page.get_by_text(ask, exact=True)).to_have_count(0)
    async with inbox_page(
        shell, Inbox(items=[], needs_review_count=0), role="tenant_staff"
    ) as page:
        empty = page.get_by_test_id("inbox-list-state-empty")
        await expect(empty).to_be_visible(timeout=WAIT)
        await expect(empty.get_by_text(ask, exact=True)).to_be_visible()
        await expect(page.get_by_test_id("inbox-connect-whatsapp")).to_have_count(0)


async def arrived_animation(page: Page, state: Inbox) -> str:
    """Open a thread, let one new message arrive, and read the bubble's animation name."""
    await rows_loaded(page)
    await open_thread(page, NAMED)
    state.thread(NAMED).insert(
        0, message(f"{NAMED[:-3]}m03", "inbound", "customer", "Just checking in again.", 0)
    )
    bubble = page.get_by_test_id("inbox-thread").locator("li", has_text="Just checking in again.")
    await expect(bubble).to_be_visible(timeout=WAIT)
    return await bubble.evaluate("el => getComputedStyle(el).animationName")


async def test_reduced_motion_removes_animations(shell: Shell) -> None:
    # Control: with motion allowed the same arrival animates, so "none" below means something.
    state = Inbox()
    async with inbox_page(shell, state) as page:
        assert await arrived_animation(page, state) == "fs-message-in"
    state = Inbox()
    async with inbox_page(shell, state, reduced_motion=True) as page:
        assert await page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
        assert await arrived_animation(page, state) == "none"


async def test_agent_avatar_loads_and_the_proxy_still_denies_by_default(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        await open_thread(page, NAMED)
        # The avatar is an inline sprite now, not an <img>: no request for the artwork is needed.
        avatar = page.get_by_test_id("inbox-thread").locator("[data-agent='support']").first
        sprite = avatar.locator("svg")
        await expect(sprite.locator("rect").first).to_be_attached(timeout=WAIT)
        assert await sprite.get_attribute("shape-rendering") == "crispEdges"
        # The thread prints no name beside it, but the sender is named in text, so it is decoration.
        assert await sprite.get_attribute("aria-hidden") == "true"
        assert await avatar.locator("img").count() == 0
        box = await sprite.bounding_box()
        assert box is not None
        assert (box["width"], box["height"]) == (32, 32)  # size 32 = the 32 grid at 1x
    async with httpx.AsyncClient(base_url=shell.base, trust_env=False, timeout=60) as http:
        artwork = await http.get("/fourstacks/agents/support.svg")
        assert artwork.status_code == 200
        assert "svg" in artwork.headers["content-type"]
        # Everything else is unchanged. The proxy refuses with a plain-text "Not found";
        # Next's own not-found page is HTML, so the body proves the proxy answered.
        for path in ("/definitely-not-a-page", "/fourstacks-but-not-artwork"):
            # `fourstacks/` needs its slash: a sibling name still goes through the proxy.
            refused = await http.get(path)
            assert (refused.status_code, refused.text) == (404, "Not found"), path
        gated = await http.get("/portal/inbox")
        assert gated.status_code in {302, 303, 307, 308}, gated.status_code
        assert gated.headers["location"].endswith("/login")
    session = {"csa_session": "synthetic-session"}
    async with httpx.AsyncClient(
        base_url=shell.base, trust_env=False, timeout=60, cookies=session
    ) as signed_in:
        # Real pages that are not adopted: with a session, only the deny branch refuses them.
        for path in ("/system", "/settings/audit"):
            refused = await signed_in.get(path)
            assert (refused.status_code, refused.text) == (404, "Not found"), path


async def avatar_frames(avatar: Any) -> list[str]:
    """Computed animation names of every frame of one sprite (a `[data-agent]` tile)."""
    await expect(avatar.locator(".fs-px-f0 rect").first).to_be_attached(timeout=WAIT)
    return await avatar.locator(".fs-px-frame").evaluate_all(
        "els => els.map(el => getComputedStyle(el).animationName)"
    )


async def test_agent_avatar_is_still_under_reduced_motion(shell: Shell) -> None:
    # Control: the empty-state avatar is an idle sprite, so with motion allowed it loops four
    # frames and "none" below means something.
    empty_state = Inbox(items=[], needs_review_count=0)
    async with inbox_page(shell, empty_state) as page:
        avatar = page.get_by_test_id("inbox-list-state-empty").locator("[data-agent='support']")
        frames = avatar.locator(".fs-px-frame")
        await expect(frames).to_have_count(4, timeout=WAIT)
        assert await avatar_frames(avatar) == ["fs-px-show"] * 4
    async with inbox_page(shell, empty_state, reduced_motion=True) as page:
        assert await page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
        avatar = page.get_by_test_id("inbox-list-state-empty").locator("[data-agent='support']")
        assert await avatar_frames(avatar) == ["none"]  # frame 0 only, and it does not animate
        await page.wait_for_timeout(600)  # long enough for the motion hook to have run
        assert await avatar_frames(avatar) == ["none"]
        frame = avatar.locator(".fs-px-f0")
        assert await frame.evaluate("el => getComputedStyle(el).opacity") == "1"


async def test_thread_avatar_is_a_static_undimmed_rest_pose_even_with_motion_allowed(
    shell: Shell,
) -> None:
    async with inbox_page(shell, Inbox()) as page:
        await rows_loaded(page)
        await open_thread(page, NAMED)
        avatar = page.get_by_test_id("inbox-thread").locator("[data-agent='support']").first
        assert await avatar_frames(avatar) == ["none"]
        await page.wait_for_timeout(600)  # an idle sprite would have gained its other frames
        assert await avatar_frames(avatar) == ["none"]
        assert await avatar.get_attribute("data-state") == "rest"
        # Not dimmed: past replies are not inactive agents.
        assert await avatar.locator("svg").evaluate("el => getComputedStyle(el).filter") == "none"


# ---- Task 13: a friendlier inbox, and a confirm step before a reply is queued ----------------

REPLY = "Yes, it arrives on Friday."
CONFIRM_ASK = "Send this reply to the customer?"


async def open_composer(page: Page, text: str = REPLY) -> Any:
    """Open the conversation where staff already took over and type a reply."""
    await rows_loaded(page)
    await open_thread(page, NEWCOMER)
    await expect(page.get_by_label("Human reply", exact=True)).to_be_visible(timeout=WAIT)
    # Held by tag: the wrapping label's text grows with the textarea's contents.
    box = page.get_by_test_id("inbox-thread").locator("textarea")
    await box.fill(text)
    return box


async def test_queue_reply_asks_for_confirmation_and_cancel_sends_nothing(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        box = await open_composer(page)
        queue = page.get_by_role("button", name="Send reply", exact=True)
        confirm = page.get_by_test_id("inbox-reply-confirm")
        await expect(confirm).to_have_count(0)
        await queue.click()
        await expect(confirm).to_be_visible()
        await expect(confirm.get_by_text(CONFIRM_ASK, exact=True)).to_be_visible()
        assert state.replies == []
        # Focus lands on the safe button, so Enter on it cannot send.
        cancel = page.get_by_test_id("inbox-reply-confirm-cancel")
        await expect(cancel).to_be_focused()
        await page.keyboard.press("Enter")
        await expect(confirm).to_have_count(0)
        await expect(queue).to_be_focused()
        await expect(box).to_have_value(REPLY)
        # Escape cancels too, and focus comes back to the button that opened the step.
        await queue.click()
        await expect(confirm).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(confirm).to_have_count(0)
        await expect(queue).to_be_focused()
        await expect(box).to_have_value(REPLY)
        # Pressing Enter on Send reply from the keyboard only opens the step.
        await page.keyboard.press("Enter")
        await expect(confirm).to_be_visible()
        await expect(page.get_by_test_id("inbox-reply-confirm-send")).not_to_be_focused()
        await asyncio.sleep(0.5)
        assert state.replies == []
        await expect(page.get_by_text("Reply queued.")).to_have_count(0)


@pytest.mark.parametrize("how", ("dblclick", "same_tick"))
async def test_queue_reply_confirm_sends_once(shell: Shell, how: str) -> None:
    state = Inbox(reply_delay=0.8)
    async with inbox_page(shell, state) as page:
        box = await open_composer(page)
        await page.get_by_role("button", name="Send reply", exact=True).click()
        send = page.get_by_test_id("inbox-reply-confirm-send")
        await expect(send).to_be_visible()
        if how == "dblclick":
            await send.dblclick()
        else:
            # Two clicks in one tick: nothing has re-rendered, so only the guard stops the second.
            await send.evaluate("el => { el.click(); el.click(); }")
        await expect(
            page.get_by_text(
                "Reply queued. Delivery status appears in the conversation.", exact=True
            )
        ).to_be_visible(timeout=WAIT)
        await asyncio.sleep(0.5)
        assert state.replies == [REPLY]
        await expect(page.get_by_test_id("inbox-reply-confirm")).to_have_count(0)
        await expect(box).to_have_value("")
        # The button is disabled with nothing typed, so focus rests in the box for the next reply.
        await expect(box).to_be_focused()


async def test_escape_does_nothing_while_the_reply_is_being_sent(shell: Shell) -> None:
    state = Inbox(reply_delay=1.5)
    async with inbox_page(shell, state) as page:
        box = await open_composer(page)
        await page.get_by_role("button", name="Send reply", exact=True).click()
        confirm = page.get_by_test_id("inbox-reply-confirm")
        await page.get_by_test_id("inbox-reply-confirm-send").click()
        await expect(page.get_by_test_id("inbox-reply-confirm-send")).to_have_text("Sending...")
        await page.keyboard.press("Escape")
        # Focus is on nothing while the buttons are disabled, so also aim Escape at the form itself.
        await page.get_by_test_id("inbox-composer").evaluate(
            "el => el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))"
        )
        await expect(page.get_by_test_id("inbox-reply-confirm-cancel")).to_be_disabled()
        await asyncio.sleep(0.3)
        # The question stays up until the request ends.
        await expect(confirm).to_be_visible()
        await expect(box).to_have_value(REPLY)
        await expect(
            page.get_by_text(
                "Reply queued. Delivery status appears in the conversation.", exact=True
            )
        ).to_be_visible(timeout=WAIT)
        await expect(confirm).to_have_count(0)
        assert state.replies == [REPLY]


@pytest.mark.parametrize("theme", ("cobalt", "night"))
@pytest.mark.parametrize("size", ((390, 844), (320, 640)))
async def test_assistant_launcher_never_covers_the_reply_buttons(
    shell: Shell, theme: str, size: tuple[int, int]
) -> None:
    """A fixed pill like the assistant's launcher (bottom right, z-40, after the app)."""
    width, height = size
    async with inbox_page(shell, Inbox(), width=width, theme=theme) as page:
        await page.set_viewport_size({"width": width, "height": height})
        await page.goto(f"{shell.base}/portal/inbox")
        await open_composer(page)
        await page.evaluate(
            """() => {
                const pill = document.createElement("div");
                pill.id = "dock-stand-in";
                pill.style.cssText = "position:fixed;bottom:20px;right:20px;width:120px;"
                    + "height:44px;z-index:40;background:red";
                document.body.appendChild(pill);
            }"""
        )
        queue = page.get_by_role("button", name="Send reply", exact=True)
        assert await on_top(queue), "Send reply is under the launcher"
        assert not await touches_corner(queue)
        await queue.click()
        for test_id in ("inbox-reply-confirm-send", "inbox-reply-confirm-cancel"):
            button = page.get_by_test_id(test_id)
            await expect(button).to_be_in_viewport()
            assert await on_top(button), (test_id, "is under the launcher")
            assert not await touches_corner(button), (test_id, "touches the launcher corner")
        # The pill really is drawn over the corner (so the check above can fail).
        assert (
            await page.evaluate(
                "() => document.elementFromPoint(innerWidth - 80, innerHeight - 42)?.id"
            )
            == "dock-stand-in"
        )


async def test_sender_and_channel_filters_work_like_the_old_controls(shell: Shell) -> None:
    # Was the All / Customers / New senders chips and the channel select: both moved into
    # the Filters panel and still combine into one request.
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        panel = await open_filters(page)
        sender = panel.get_by_role("group", name="Sender")
        await expect(sender.get_by_role("radio")).to_have_count(3)
        await expect(sender.get_by_role("radio", name="Anyone")).to_be_checked()

        async def choose(group: str, name: str, key: str, expected: str) -> dict[str, list[str]]:
            before = len(state.list_queries)
            await panel.get_by_role("group", name=group).get_by_role("radio", name=name).check()
            await eventually(lambda: len(state.list_queries) > before)
            await asyncio.sleep(0.3)
            latest = parse_qs(state.list_queries[-1])
            assert latest.get(key, [""])[0] == expected, latest
            assert "needs_review" not in latest, latest
            return latest

        await choose("Sender", "Customers", "sender", "known")
        await rows_loaded(page, 2)
        await choose("Sender", "New senders", "sender", "new")
        await rows_loaded(page, 2)
        await choose("Sender", "Anyone", "sender", "")
        await rows_loaded(page, 4)
        # Channel and sender still combine into one request.
        await choose("Channel", "WhatsApp", "channel", "whatsapp")
        latest = await choose("Sender", "New senders", "sender", "new")
        assert latest["channel"] == ["whatsapp"] and latest["sender"] == ["new"]
        await rows_loaded(page, 1)
        await expect(row(page, UNNAMED)).to_have_count(1)


async def test_search_clear_button_resets(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        search = page.get_by_test_id("inbox-search")
        clear = page.get_by_test_id("inbox-search-clear")
        await expect(clear).to_have_count(0)
        await search.fill("Ai")
        await expect(clear).to_be_visible()
        await eventually(lambda: "q=Ai" in state.list_queries[-1])
        await clear.click()
        await expect(search).to_have_value("")
        await expect(search).to_be_focused()
        await expect(clear).to_have_count(0)
        await eventually(lambda: "q=" not in state.list_queries[-1])
        await asyncio.sleep(0.5)
        assert "q=" not in state.list_queries[-1], state.list_queries
        # One character still shows the existing hint, and the clear button removes it.
        await search.fill("A")
        await expect(page.get_by_text("Type at least two characters to search.")).to_be_visible()
        await clear.click()
        await expect(page.get_by_text("Type at least two characters to search.")).to_have_count(0)


async def test_rows_show_preview_time_and_review_marker(shell: Shell) -> None:
    state = Inbox()
    long_preview = "Could you check whether my order can still be changed before it ships out? " * 2
    state.items[0]["last_message"]["preview"] = long_preview.strip()
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        named = row(page, NAMED)
        # Avatar: a round initials circle.
        avatar = named.locator("span[aria-hidden='true']").first
        await expect(avatar).to_have_text("AR")
        assert await avatar.evaluate("el => getComputedStyle(el).borderRadius") == "50%"
        # Relative time, name and a one-line preview that ends in an ellipsis.
        await expect(named.get_by_test_id("inbox-row-time")).to_have_text(
            re.compile(r"^(Just now|\d+ min)$")
        )
        preview = named.get_by_test_id("inbox-row-preview")
        await expect(preview).to_contain_text("Could you check whether my order")
        style = await preview.evaluate(
            """el => ({
                ellipsis: getComputedStyle(el).textOverflow,
                wrap: getComputedStyle(el).whiteSpace,
                cut: el.scrollWidth > el.clientWidth,
                lines: Math.round(
                    el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight)
                ),
            })"""
        )
        assert style == {"ellipsis": "ellipsis", "wrap": "nowrap", "cut": True, "lines": 1}, style
        # The review marker is words plus an icon, only on rows with a held reply.
        for identifier, expected in ((NAMED, 1), (UNNAMED, 0), (NEWCOMER, 1), (LONG, 0)):
            marker = row(page, identifier).get_by_test_id("inbox-row-review")
            await expect(marker).to_have_count(expected)
        marker = named.get_by_test_id("inbox-row-review")
        await expect(marker).to_have_text("Needs review")
        await expect(marker.locator("svg")).to_have_count(1)
        # A known customer has no sender pill any more (it was on every row); a new sender keeps
        # the dashed "New sender" label. The channel is an icon named for assistive technology.
        await expect(named).not_to_contain_text("Customer")
        await expect(row(page, NEWCOMER)).to_contain_text("New sender")
        badge = named.get_by_test_id("inbox-channel-badge")
        await expect(badge).to_have_text("Telegram")
        await expect(badge).to_have_role("img")
        await expect(badge).to_have_accessible_name("Telegram")


async def on_top(button: Any) -> bool:
    """Whether the centre of the control is the control itself, not something drawn over it."""
    return await button.evaluate(
        """el => {
            const r = el.getBoundingClientRect();
            const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
            return hit === el || el.contains(hit);
        }"""
    )


async def touches_corner(button: Any) -> bool:
    """Whether the control's box meets the launcher stand-in's box (bottom 20, right 20, 120x44)."""
    return await button.evaluate(
        """el => {
            const r = el.getBoundingClientRect();
            const left = innerWidth - 20 - 120, top = innerHeight - 20 - 44;
            const right = innerWidth - 20, bottom = innerHeight - 20;
            return r.right > left && r.bottom > top && r.left < right && r.top < bottom;
        }"""
    )


async def test_composer_sticky_at_390(shell: Shell) -> None:
    state = Inbox()
    async with inbox_page(shell, state, width=390) as page:
        await open_composer(page)
        composer = page.get_by_test_id("inbox-composer")
        queue = page.get_by_role("button", name="Send reply", exact=True)
        height = await page.evaluate("window.innerHeight")

        async def in_view() -> dict[str, float]:
            box = await composer.bounding_box()
            assert box is not None
            return {"top": box["y"], "bottom": box["y"] + box["height"], "height": height}

        # Opening a conversation brings the pane into view; from there and at the foot of the
        # page the reply box and its button stay on screen, with nothing drawn over them.
        for position in ("window.scrollY", "document.body.scrollHeight"):
            await page.evaluate(f"window.scrollTo(0, {position})")
            await asyncio.sleep(0.2)
            seen = await in_view()
            assert 0 <= seen["top"] and seen["bottom"] <= height, (position, seen)
            await expect(queue).to_be_in_viewport()
            assert await on_top(queue), ("covered", position)
        # Scrolling the messages up does not move the composer either.
        await page.get_by_role("log").evaluate("el => { el.scrollTop = 0; }")
        seen = await in_view()
        assert seen["bottom"] <= height, seen
        # Opening the confirm step keeps both of its buttons on screen as well.
        await queue.click()
        await expect(page.get_by_test_id("inbox-reply-confirm-send")).to_be_in_viewport()
        await expect(page.get_by_test_id("inbox-reply-confirm-cancel")).to_be_in_viewport()
        # Nothing floats over the buttons (the assistant's round launcher sits bottom right).
        assert await on_top(page.get_by_test_id("inbox-reply-confirm-send"))
        assert await on_top(page.get_by_test_id("inbox-reply-confirm-cancel"))
        assert await on_top(queue)


@pytest.mark.parametrize("theme", ("cobalt", "night", "bureau"))
async def test_inbox_no_overflow_at_five_widths_in_three_themes(shell: Shell, theme: str) -> None:
    state = Inbox(filter_search=True)
    async with inbox_page(shell, state, width=WIDTHS[0], theme=theme) as page:
        await expect(page.locator("html")).to_have_attribute("data-theme", theme)
        for width in WIDTHS:
            await page.set_viewport_size({"width": width, "height": 900})
            await page.goto(f"{shell.base}/portal/inbox")
            await rows_loaded(page)
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], ("list", theme, width, measured)
            await open_thread(page, NEWCOMER)
            await (
                page.get_by_test_id("inbox-thread")
                .locator("textarea")
                .fill("Thank you, it ships tomorrow.")
            )
            await page.get_by_role("button", name="Send reply", exact=True).click()
            await expect(page.get_by_test_id("inbox-reply-confirm")).to_be_visible()
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], ("confirm", theme, width, measured)
            # No-match state with its one next action.
            await page.get_by_test_id("inbox-reply-confirm-cancel").click()
            if width < 768:
                await page.get_by_role("button", name="Back to conversations").click()
            await page.get_by_test_id("inbox-search").fill("zzzz")
            await expect(page.get_by_test_id("inbox-no-match")).to_be_visible(timeout=WAIT)
            measured = await page_width(page)
            assert measured["scroll"] <= measured["inner"], ("no-match", theme, width, measured)


async def test_how_it_works_text_sits_behind_a_closed_arrow(shell: Shell) -> None:
    mechanics = "Take over a chat before you reply."
    async with inbox_page(shell, Inbox()) as page:
        await rows_loaded(page)
        row_box = await row(page, NAMED).bounding_box()
        assert row_box is not None
        # The long explanation is still on the page, word for word, but closed and out of sight.
        notice = page.get_by_text(mechanics)
        await expect(notice).to_have_count(1)
        await expect(notice).to_be_hidden()
        await expect(page.get_by_test_id("inbox-howto")).to_have_attribute("data-open", "false")
        # The list is not pushed down the page by it.
        assert row_box["y"] < 700, row_box
        await page.get_by_test_id("inbox-howto-toggle").click()
        await expect(notice).to_be_visible()
        await expect(
            page.get_by_text("A sent reply waits to be delivered; its status shows under it.")
        ).to_be_visible()


async def test_queue_reply_points_at_its_question_only_while_open_and_a_release_closes_it(
    shell: Shell,
) -> None:
    state = Inbox()
    async with inbox_page(shell, state) as page:
        await open_composer(page)
        queue = page.get_by_role("button", name="Send reply", exact=True)
        confirm = page.get_by_test_id("inbox-reply-confirm")
        # aria-controls names an element only while that element exists.
        await expect(queue).not_to_have_attribute("aria-controls", re.compile(r".+"))
        await queue.click()
        await expect(confirm).to_be_visible()
        controls = await queue.get_attribute("aria-controls")
        assert controls and await page.locator(f"[id='{controls}']").count() == 1
        # The conversation is released to the AI (seen by the next poll): the question goes too.
        row = next(item for item in state.items if item["id"] == NEWCOMER)
        row["human_muted"], row["owned_by"] = False, None
        await expect(confirm).to_have_count(0, timeout=15_000)
        await expect(page.get_by_label("Human reply", exact=True)).to_have_count(0)
        assert state.replies == []


async def test_the_thread_shows_the_assistants_note(shell: Shell) -> None:
    # Model text shaped by a customer's message: it must come out as inert text, never markup.
    hostile = '<img src=x onerror="window.__noted=1"> [open](https://evil.example) **bold**'
    state = Inbox(notes={NAMED: {"text": hostile, "at": ago(3)}})
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        await open_thread(page, UNNAMED)
        await expect(page.get_by_test_id("inbox-handoff-note")).to_have_count(0)
        await open_thread(page, NAMED)
        note = page.get_by_test_id("inbox-handoff-note")
        await expect(note).to_be_visible(timeout=WAIT)
        text = await note.inner_text()
        assert text.startswith("Note from the assistant (")
        assert text.endswith(f"): {hostile}")
        # One line under "who is replying", inside the thread header.
        header = page.get_by_test_id("inbox-thread").locator("header")
        assert await header.get_by_test_id("inbox-handoff-note").count() == 1
        # Text only: no element, link or image was made from the note.
        assert await note.locator("*").count() == 0
        assert await page.evaluate("window.__noted === undefined")
        # The list rows never show it.
        await expect(row(page, NAMED)).not_to_contain_text("Note from the assistant")


PICTURE_ASSET = "b0000000-0000-4000-8000-0000000000a1"
CAPTION = "DuitNow QR: Kedai Contoh"


def picture_thread(identifier: str) -> list[dict[str, Any]]:
    """Newest first: the text reply, the picture reply sent just before it, the customer's ask."""
    stem = identifier[:-3]
    return [
        message(f"{stem}m03", "outbound", "ai", "Scan the QR to pay.", 18),
        message(f"{stem}m02", "outbound", "ai", CAPTION, 19, picture=PICTURE_ASSET),
        message(f"{stem}m01", "inbound", "customer", "How do I pay?", 21),
    ]


async def test_an_image_reply_shows_its_thumbnail_and_caption(shell: Shell) -> None:
    state = Inbox(messages={NAMED: picture_thread(NAMED)})
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        await open_thread(page, NAMED, "Scan the QR to pay.")
        thread = page.get_by_test_id("inbox-thread")
        # Exactly one thumbnail: the text reply and the customer's message have none.
        picture = thread.get_by_test_id("inbox-message-picture")
        await expect(picture).to_have_count(1)
        await expect(picture).to_be_visible(timeout=WAIT)
        await expect(picture).to_have_attribute("alt", CAPTION)
        await expect(picture).to_have_attribute("loading", "lazy")
        await expect(picture).to_have_attribute("src", f"/console/v1/media/{PICTURE_ASSET}")
        # The caption stays under it, and the image really decoded at its fixed size.
        await expect(thread.get_by_text(CAPTION, exact=True)).to_be_visible()
        await page.wait_for_function(
            "() => { const image = document.querySelector('[data-testid=inbox-message-picture]');"
            " return image !== null && image.complete && image.naturalWidth === 160; }",
            timeout=WAIT,
        )
        box = await picture.bounding_box()
        assert box is not None and round(box["width"]) == 160 and round(box["height"]) == 160
        above = await thread.get_by_text(CAPTION, exact=True).bounding_box()
        assert above is not None and box["y"] + box["height"] <= above["y"] + 1
        assert state.picture_requests == [f"media/{PICTURE_ASSET}"]


async def test_an_image_reply_the_viewer_may_not_read_hides_its_thumbnail(shell: Shell) -> None:
    state = Inbox(messages={NAMED: picture_thread(NAMED)}, picture_status=404)
    async with inbox_page(shell, state) as page:
        await rows_loaded(page)
        await open_thread(page, NAMED, "Scan the QR to pay.")
        thread = page.get_by_test_id("inbox-thread")
        await expect(thread.get_by_text(CAPTION, exact=True)).to_be_visible(timeout=WAIT)
        await eventually(lambda: len(state.picture_requests) > 0)
        await expect(thread.get_by_test_id("inbox-message-picture")).to_have_count(0)
