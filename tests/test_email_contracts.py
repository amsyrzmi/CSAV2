"""Pure contract tests for the staff email assistant: parsing, bounds, PKCE, settings."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime
from email import message_from_bytes
from typing import Any
from uuid import uuid4

import httpx
import pytest
from csa.email import gmail, recipients
from csa.email.models import (
    ComposeRequest,
    DraftOut,
    DraftProposal,
    DraftUpdate,
    ReplyRequest,
    ThreadAnalysis,
    draft_version,
    validated_draft,
)
from pydantic import ValidationError


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_pkce_pair_is_s256_and_bounded() -> None:
    verifier, challenge = gmail.pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert "=" not in challenge and len(challenge) == 43


def test_body_extraction_prefers_plain_text_then_html() -> None:
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {
                "mimeType": "text/html",
                "body": {"data": _b64("<p>Hi <b>there</b></p><script>x</script>")},
            },
            {"mimeType": "text/plain", "body": {"data": _b64("Hi there\x00plain")}},
        ],
    }
    assert gmail._extract_body(payload) == "Hi thereplain"
    html_only = {
        "mimeType": "text/html",
        "body": {"data": _b64("<div>Line one</div><div>Two &amp; three</div>")},
    }
    assert gmail._extract_body(html_only) == "Line one\n Two & three"
    assert gmail._extract_body({"mimeType": "text/plain", "body": {}}) is None


def test_body_is_capped() -> None:
    huge = {"mimeType": "text/plain", "body": {"data": _b64("x" * 30000)}}
    body = gmail._extract_body(huge)
    assert body is not None and len(body) == gmail.MAX_BODY_CHARS


def test_reply_mime_is_plain_text_with_validated_headers() -> None:
    raw = gmail.build_reply_mime(
        to="aina@example.test", subject="Re: Order", body="Thanks.\nEmpat Tingkat"
    )
    message = message_from_bytes(raw)
    assert message["To"] == "aina@example.test"
    assert message["Subject"] == "Re: Order"
    assert message.get_content_type() == "text/plain"
    assert "Bcc" not in message


def test_authorization_url_requests_offline_readonly_and_compose_only() -> None:
    client = gmail.GmailClient(
        "client-id",
        gmail.SecretStr("client-secret"),
        "https://console.example/console/v1/email/oauth/callback",
        None,  # type: ignore[arg-type]
    )
    url = client.authorization_url(state="st", code_challenge="ch")
    assert "gmail.readonly" in url and "gmail.compose" in url
    assert "gmail.modify" not in url and "gmail.send" not in url
    assert "access_type=offline" in url and "code_challenge_method=S256" in url


def test_draft_contract_rejects_header_injection_and_display_names() -> None:
    mailbox = uuid4()
    with pytest.raises(ValidationError):
        validated_draft(mailbox, "t1", "aina@example.test", "Re: x\r\nBcc: y@z", "body")
    with pytest.raises(ValidationError):
        validated_draft(mailbox, "t1", "Aina <aina@example.test>", "Re: x", "body")
    with pytest.raises(ValidationError):
        validated_draft(mailbox, "t1", "aina@example.test", "Re: x", "x" * 20001)
    ok = validated_draft(mailbox, "t1", "aina@example.test", "Re: x", "fine")
    assert ok.recipient == "aina@example.test"


def test_model_outputs_are_strict_on_structure() -> None:
    base = {"category": "complaint", "priority": "high", "language": "en", "summary": "s"}
    # Structure stays strict: an object, a non-empty summary, a list of string action items.
    for broken in (
        [base],
        "complaint",
        {**base, "summary": "  "},
        {k: v for k, v in base.items() if k != "summary"},
        {**base, "action_items": "call back"},
        {**base, "action_items": [1]},
        {**base, "category": None},
        {**base, "priority": 3},
    ):
        with pytest.raises(ValidationError):
            ThreadAnalysis.model_validate(broken)
    with pytest.raises(ValidationError):
        ThreadAnalysis.model_validate_json("[]")
    analysis = ThreadAnalysis.model_validate(
        {
            "category": "complaint",
            "priority": "high",
            "language": "ms",
            "summary": "s",
            "action_items": [" a ", ""],
        }
    )
    assert analysis.action_items == ["a"]
    # Unknown keys from the model are dropped, never stored or acted on.
    extra = ThreadAnalysis.model_validate({**base, "tool_call": "x", "reason": "y"})
    assert "tool_call" not in extra.model_dump() and "reason" not in extra.model_dump()


def test_model_output_enums_are_normalised_not_rejected() -> None:
    """The provider's JSON mode carries no schema, so the model drifts from the Literals."""
    analysis = ThreadAnalysis.model_validate_json(
        json.dumps(
            {
                "category": "Shipping Inquiry",
                "priority": " High ",
                "language": "English",
                "summary": "s",
            }
        )
    )
    assert (analysis.category, analysis.priority, analysis.language) == ("other", "high", "other")
    spaced = ThreadAnalysis.model_validate(
        {"category": "Order or Delivery", "priority": "urgent", "language": "MS", "summary": "s"}
    )
    assert (spaced.category, spaced.priority, spaced.language) == (
        "order_or_delivery",
        "normal",
        "ms",
    )


def test_model_output_text_is_truncated_not_rejected() -> None:
    analysis = ThreadAnalysis.model_validate(
        {
            "category": "complaint",
            "priority": "high",
            "language": "en",
            "summary": " " + "s" * 700,
            "action_items": ["x" * 201, "b", " ", "c", "d", "e", "f", "g"],
        }
    )
    assert analysis.summary == "s" * 600
    assert analysis.action_items == ["x" * 200, "b", "c", "d", "e"]


def test_analysis_prompt_lists_every_allowed_value() -> None:
    from typing import get_args

    from csa.email import models, service

    for value in (
        *get_args(models.Category),
        *get_args(models.Priority),
        *get_args(models.AnalysisLanguage),
    ):
        assert f'"{value}"' in service.ANALYSIS_SYSTEM, value


def test_draft_proposal_is_strict() -> None:
    with pytest.raises(ValidationError):
        DraftProposal.model_validate({"subject": "", "body": "x"})
    with pytest.raises(ValidationError):
        DraftProposal.model_validate({"subject": "s", "body": "x", "send": True})


# --- MIME with cc/bcc/threading ---------------------------------------------


def test_message_mime_carries_cc_bcc_and_threading_headers() -> None:
    raw = gmail.build_message_mime(
        to=["aina@example.test"],
        cc=["boss@example.test", "ops@example.test"],
        bcc=["audit@example.test"],
        subject="Re: Order",
        body="Thanks.",
        in_reply_to="<abc@mail.example.test>",
        references="<root@mail.example.test> <abc@mail.example.test>",
    )
    message = message_from_bytes(raw)
    assert message["To"] == "aina@example.test"
    assert message["Cc"] == "boss@example.test, ops@example.test"
    assert message["In-Reply-To"] == "<abc@mail.example.test>"
    assert message["References"] == "<root@mail.example.test> <abc@mail.example.test>"
    # Gmail delivers to Bcc recipients from this header and strips it from the sent copy.
    assert message["Bcc"] == "audit@example.test"
    assert message.get_content_type() == "text/plain"


def test_message_mime_without_cc_bcc_has_no_such_headers() -> None:
    message = message_from_bytes(
        gmail.build_message_mime(to=["a@example.test"], subject="s", body="b")
    )
    assert message["Cc"] is None and message["Bcc"] is None
    assert message["In-Reply-To"] is None and message["References"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"to": ["aina@example.test\r\nBcc: x@example.test"]},
        {"to": ["a@example.test"], "cc": ["Aina <aina@example.test>"]},
        {"to": ["a@example.test"], "bcc": ["a@b"]},
        {"to": ["a@example.test"], "subject": "Re: x\r\nBcc: y@example.test"},
        {"to": ["a@example.test"], "in_reply_to": "<a@b.test>\r\nBcc: y@example.test"},
        {"to": ["a@example.test"], "references": "<a@b.test>\nX: y"},
        {"to": []},
        {"to": ["a" * 250 + "@example.test"]},
        {"to": ["a@example.test"], "body": ""},
        {"to": ["a@example.test"], "body": "   \n "},
        {"to": ["a@example.test"], "in_reply_to": "<" + "x" * 600 + "@mail.example.test>"},
        {"to": ["a@example.test"], "references": " ".join(["<x@mail.example.test>"] * 200)},
    ],
)
def test_message_mime_rejects_injection_and_bad_addresses(kwargs: dict[str, Any]) -> None:
    args: dict[str, Any] = {"subject": "Re: x", "body": "b", **kwargs}
    with pytest.raises(gmail.GmailError) as err:
        gmail.build_message_mime(**args)
    assert err.value.code == "invalid_message"


def test_message_mime_caps_total_recipients_at_twenty() -> None:
    twenty = [f"p{i}@example.test" for i in range(20)]
    gmail.build_message_mime(
        to=twenty[:10], cc=twenty[10:15], bcc=twenty[15:], subject="s", body="b"
    )
    with pytest.raises(gmail.GmailError) as err:
        gmail.build_message_mime(
            to=twenty[:10], cc=twenty[10:], bcc=["extra@example.test"], subject="s", body="b"
        )
    assert err.value.code == "invalid_message"


def test_reply_mime_delegates_to_message_mime() -> None:
    message = message_from_bytes(gmail.build_reply_mime(to="a@example.test", subject="s", body="b"))
    assert message["To"] == "a@example.test"
    with pytest.raises(gmail.GmailError):
        gmail.build_reply_mime(to="a@example.test\r\nBcc: x@y.test", subject="s", body="b")


# --- Gmail client over a fake transport -------------------------------------


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> gmail.GmailClient:
    return gmail.GmailClient(
        "client-id",
        gmail.SecretStr("client-secret"),
        "https://console.example/console/v1/email/oauth/callback",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


ACCESS = gmail.SecretStr("access-token")


async def test_send_message_posts_raw_and_thread_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "msg-1", "threadId": "t1"})

    mime = gmail.build_message_mime(to=["a@example.test"], subject="s", body="b")
    message_id = await _client(handler).send_message(ACCESS, thread_id="t1", mime=mime)
    assert message_id == "msg-1"
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path.endswith("/users/me/messages/send")
    assert request.headers["authorization"] == "Bearer access-token"
    payload = json.loads(request.content)
    assert payload["threadId"] == "t1"
    assert base64.urlsafe_b64decode(payload["raw"]) == mime


async def test_send_message_without_thread_omits_thread_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "msg-2"})

    await _client(handler).send_message(ACCESS, thread_id=None, mime=b"x")
    assert "threadId" not in json.loads(seen[0].content)


async def test_send_message_timeout_is_provider_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(gmail.GmailError) as err:
        await _client(handler).send_message(ACCESS, thread_id=None, mime=b"x")
    assert err.value.code == "provider_unreachable"


async def test_send_message_connection_error_is_provider_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(gmail.GmailError) as err:
        await _client(handler).send_message(ACCESS, thread_id=None, mime=b"x")
    assert err.value.code == "provider_unreachable"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "send_refused"),
        (404, "send_refused"),
        (422, "send_refused"),
        (401, "provider_unauthorized"),
        (403, "provider_unauthorized"),
        (429, "provider_rate_limited"),
    ],
)
async def test_send_message_4xx_is_a_clear_refusal(status: int, code: str) -> None:
    with pytest.raises(gmail.GmailError) as err:
        await _client(lambda r: httpx.Response(status, json={"error": "no"})).send_message(
            ACCESS, thread_id=None, mime=b"x"
        )
    assert err.value.code == code


@pytest.mark.parametrize("status", [408, 409, 499])
async def test_send_message_ambiguous_4xx_is_provider_unreachable(status: int) -> None:
    # Timeout, conflict and client-closed may follow a delivered message: not a refusal.
    with pytest.raises(gmail.GmailError) as err:
        await _client(lambda r: httpx.Response(status)).send_message(
            ACCESS, thread_id=None, mime=b"x"
        )
    assert err.value.code == "provider_unreachable"


async def test_send_message_5xx_is_ambiguous_not_refused() -> None:
    with pytest.raises(gmail.GmailError) as err:
        await _client(lambda r: httpx.Response(503)).send_message(ACCESS, thread_id=None, mime=b"x")
    assert err.value.code == "provider_unreachable"


def _messages_pages(
    pages: dict[str | None, tuple[list[str], str | None]], seen: list[httpx.Request]
) -> Callable[[httpx.Request], httpx.Response]:
    """users.messages.list, newest message first; each entry is one message's threadId."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET" and request.url.path.endswith("/users/me/messages")
        thread_ids, token = pages[request.url.params.get("pageToken")]
        body: dict[str, Any] = {
            "messages": [{"id": f"m{i}-{tid}", "threadId": tid} for i, tid in enumerate(thread_ids)]
        }
        if token:
            body["nextPageToken"] = token
        return httpx.Response(200, json=body)

    return handler


async def test_recent_threads_follow_the_latest_message_not_creation() -> None:
    """Gmail's threads.list orders by thread creation; messages.list orders by message time."""
    seen: list[httpx.Request] = []
    # An old conversation ("old-1a15") just got a reply, so its message is the newest; a
    # newer-created but quiet conversation ("new-1a18") comes later.
    pages = {None: (["old-1a15", "new-1a18", "old-1a15"], None)}
    ids, token = await _client(_messages_pages(pages, seen)).list_recent_thread_ids(
        ACCESS, query="newer_than:90d -in:chats", limit=20
    )
    assert ids == ["old-1a15", "new-1a18"] and token is None
    assert seen[0].url.params["q"] == "newer_than:90d -in:chats"
    assert seen[0].url.params["maxResults"] == "20" and "pageToken" not in seen[0].url.params


async def test_recent_threads_collapse_duplicates_and_stop_at_the_limit() -> None:
    seen: list[httpx.Request] = []
    # Page sizes follow the threads still needed, so a page never ends past the limit and the
    # token returned is exactly where the next load starts.
    first = ["a", "b", "a", "c", "b"]  # three distinct threads in five messages
    pages = {None: (first, "tok-2"), "tok-2": (["c", "d"], "tok-3"), "tok-3": (["e"], "tok-4")}
    ids, token = await _client(_messages_pages(pages, seen)).list_recent_thread_ids(
        ACCESS, query="q", limit=5
    )
    assert ids == ["a", "b", "c", "d", "e"] and token == "tok-4"
    assert [r.url.params["maxResults"] for r in seen] == ["5", "2", "1"]
    assert [r.url.params.get("pageToken") for r in seen] == [None, "tok-2", "tok-3"]


async def test_recent_threads_read_at_most_three_pages() -> None:
    seen: list[httpx.Request] = []
    pages = {None: (["a"], "tok-2"), "tok-2": (["a"], "tok-3"), "tok-3": (["b"], "tok-4")}
    ids, token = await _client(_messages_pages(pages, seen)).list_recent_thread_ids(
        ACCESS, query="q", limit=20
    )
    assert ids == ["a", "b"] and token == "tok-4" and len(seen) == 3
    assert seen[0].url.params["maxResults"] == "20"


async def test_recent_threads_continue_from_the_page_token() -> None:
    seen: list[httpx.Request] = []
    pages = {"tok-4": (["f", "g"], None)}
    ids, token = await _client(_messages_pages(pages, seen)).list_recent_thread_ids(
        ACCESS, query="q", limit=20, page_token="tok-4"
    )
    assert ids == ["f", "g"] and token is None
    assert seen[0].url.params["pageToken"] == "tok-4"


async def test_recent_threads_empty_result_is_done() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"resultSizeEstimate": 0})

    ids, token = await _client(handler).list_recent_thread_ids(ACCESS, query="q", limit=20)
    assert ids == [] and token is None and len(seen) == 1


async def test_recent_threads_reject_a_malformed_list() -> None:
    with pytest.raises(gmail.GmailError) as err:
        await _client(lambda r: httpx.Response(200, json={"messages": "x"})).list_recent_thread_ids(
            ACCESS, query="q", limit=20
        )
    assert err.value.code == "provider_invalid_response"


def _message(
    msg_id: str,
    sender: str,
    *,
    reply_to: str | None = None,
    extra: dict[str, str] | None = None,
    date: str,
) -> dict[str, Any]:
    headers = {
        "From": sender,
        "Subject": "Order",
        "Date": date,
        "Message-ID": f"<{msg_id}@mail.example.test>",
        **({"Reply-To": reply_to} if reply_to else {}),
        **(extra or {}),
    }
    return {
        "id": msg_id,
        "snippet": "hi",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": k, "value": v} for k, v in headers.items()],
            "body": {"data": _b64("hello")},
        },
    }


async def test_fetch_thread_picks_latest_non_owner_sender_and_reply_to() -> None:
    thread = {
        "id": "t1",
        "messages": [
            _message(
                "m1",
                "Aina <aina@customer.test>",
                reply_to="support@customer.test",
                date="Mon, 26 Oct 2026 09:00:00 +0000",
            ),
            _message(
                "m2",
                "Owner <Owner@Example.test>",
                date="Mon, 26 Oct 2026 10:00:00 +0000",
                extra={
                    "References": "<m1@mail.example.test>",
                    "In-Reply-To": "<m1@mail.example.test>",
                },
            ),
        ],
    }
    fetched = await _client(lambda r: httpx.Response(200, json=thread)).fetch_thread(
        ACCESS, "t1", owner_address="owner@example.test"
    )
    assert fetched.latest_message_id == "m2"
    assert fetched.latest_from == "Owner <Owner@Example.test>"
    assert fetched.latest_rfc_message_id == "<m2@mail.example.test>"
    assert fetched.latest_references == "<m1@mail.example.test>"
    assert fetched.latest_reply_to is None
    assert fetched.latest_non_owner_from == "Aina <aina@customer.test>"
    assert fetched.latest_non_owner_reply_to == "support@customer.test"
    assert fetched.sender == "Owner <Owner@Example.test>"


async def test_fetch_thread_with_only_owner_messages_has_no_non_owner_target() -> None:
    thread = {
        "id": "t1",
        "messages": [_message("m1", "owner@example.test", date="Mon, 26 Oct 2026 09:00:00 +0000")],
    }
    fetched = await _client(lambda r: httpx.Response(200, json=thread)).fetch_thread(
        ACCESS, "t1", owner_address="owner@example.test"
    )
    assert fetched.latest_non_owner_from is None and fetched.latest_non_owner_reply_to is None


@pytest.mark.parametrize(
    "error",
    [
        {"code": 403, "errors": [{"reason": "userRateLimitExceeded"}]},
        {"code": 403, "errors": [{"domain": "usageLimits", "reason": "rateLimitExceeded"}]},
        {"code": 403, "errors": [{"reason": "other"}, {"reason": "dailyLimitExceeded"}]},
        {"code": 403, "status": "rateLimitExceeded"},
    ],
)
async def test_gmail_quota_403_is_a_rate_limit(
    error: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    body = {"error": {**error, "message": "Synthetic quota text for alias@customer.test"}}
    client = _client(lambda r: httpx.Response(403, json=body))
    with pytest.raises(gmail.GmailError) as sent:
        await client.send_message(ACCESS, thread_id=None, mime=b"x")
    assert sent.value.code == "provider_rate_limited"
    # The shared JSON reader (sync, drafts, tokens) reads it the same way.
    with pytest.raises(gmail.GmailError) as listed:
        await client.list_recent_thread_ids(ACCESS, query="q", limit=5)
    assert listed.value.code == "provider_rate_limited"
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "Synthetic quota text" not in logged and "customer.test" not in logged
    assert "Synthetic quota text" not in str(sent.value) + str(listed.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, json={"error": {"errors": [{"reason": "insufficientPermissions"}]}}),
        httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}}),
        httpx.Response(403, content=b"not json"),
        httpx.Response(403, json=["userRateLimitExceeded"]),
    ],
)
async def test_other_403s_stay_unauthorized(response: httpx.Response) -> None:
    client = _client(lambda r: response)
    with pytest.raises(gmail.GmailError) as sent:
        await client.send_message(ACCESS, thread_id=None, mime=b"x")
    assert sent.value.code == "provider_unauthorized"
    with pytest.raises(gmail.GmailError) as listed:
        await client.list_recent_thread_ids(ACCESS, query="q", limit=5)
    assert listed.value.code == "provider_unauthorized"


async def _fetched(*messages: dict[str, Any]) -> gmail.FetchedThread:
    thread = {"id": "t1", "messages": list(messages)}
    return await _client(lambda r: httpx.Response(200, json=thread)).fetch_thread(
        ACCESS, "t1", owner_address="owner@example.test"
    )


async def test_received_at_is_internal_date_before_the_date_header() -> None:
    message = _message("m1", "a@customer.test", date="Mon, 06 Apr 2026 09:00:00 +0000")
    message["internalDate"] = "1793000000000"
    fetched = await _fetched(message)
    assert fetched.received_at == datetime.fromtimestamp(1793000000, tz=UTC)
    # Without internalDate the Date header is used; a future one counts as now, never later.
    del message["internalDate"]
    assert (await _fetched(message)).received_at == datetime(2026, 4, 6, 9, tzinfo=UTC)
    future = _message("m2", "a@customer.test", date="Mon, 01 Jan 2120 09:00:00 +0000")
    before = datetime.now(UTC)
    received = (await _fetched(future)).received_at
    assert received is not None and before <= received <= datetime.now(UTC)
    undated = _message("m3", "a@customer.test", date="")
    assert (await _fetched(undated)).received_at is None


async def test_a_sent_labelled_alias_is_never_the_reply_target() -> None:
    customer = _message("m1", "Aina <aina@customer.test>", date="Mon, 26 Oct 2026 09:00:00 +0000")
    alias = _message("m2", "Sales <sales@example.test>", date="Mon, 26 Oct 2026 10:00:00 +0000")
    alias["labelIds"] = ["SENT"]
    fetched = await _fetched(customer, alias)
    assert fetched.latest_message_id == "m2" and "SENT" in fetched.labels
    assert fetched.latest_non_owner_from == "Aina <aina@customer.test>"
    only_alias = await _fetched(alias)
    assert only_alias.latest_non_owner_from is None


# --- recipient rules --------------------------------------------------------


def test_classify_directory_match_is_inside_case_insensitive() -> None:
    result = recipients.classify(
        ["Aina@Partner.TEST", "stranger@partner.test"],
        directory_emails=frozenset({"aina@partner.test"}),
        owner_address="owner@example.test",
    )
    assert result == [
        recipients.Recipient("Aina@Partner.TEST", False),
        recipients.Recipient("stranger@partner.test", True),
    ]


def test_classify_same_company_domain_is_inside() -> None:
    result = recipients.classify(
        ["new.hire@EXAMPLE.test", "x@other.test"],
        directory_emails=frozenset(),
        owner_address="Owner@example.test",
    )
    assert [r.outside for r in result] == [False, True]


def test_classify_public_provider_domain_is_not_company_membership() -> None:
    result = recipients.classify(
        ["friend@gmail.com", "colleague@yahoo.com"],
        directory_emails=frozenset(),
        owner_address="me@gmail.com",
    )
    assert [r.outside for r in result] == [True, True]


def test_public_providers_list() -> None:
    assert recipients.PUBLIC_PROVIDERS == frozenset(
        {
            "gmail.com",
            "googlemail.com",
            "yahoo.com",
            "outlook.com",
            "hotmail.com",
            "live.com",
            "icloud.com",
            "me.com",
            "aol.com",
            "proton.me",
            "protonmail.com",
        }
    )


def test_has_staff_placeholder() -> None:
    assert recipients.has_staff_placeholder("Dear Aina, price is [STAFF: confirm price].")
    assert recipients.has_staff_placeholder("[staff: fill in]")
    assert not recipients.has_staff_placeholder("Dear Aina, the price is RM10. [Staff] is fine")


def test_trim_references_drops_whole_ids_from_the_front() -> None:
    ids = [f"<id{i:03d}-{'x' * 30}@mail.example.test>" for i in range(100)]
    trimmed = gmail.trim_references(" ".join(ids))
    assert trimmed is not None and len(trimmed) <= gmail.MAX_REFERENCES_CHARS
    kept = trimmed.split(" ")
    assert kept == ids[-len(kept) :]
    assert gmail.trim_references("  ") is None and gmail.trim_references(None) is None
    assert gmail.trim_references("<a@b.test>\r\n <c@d.test>") == "<a@b.test> <c@d.test>"


async def test_fetch_thread_trims_long_references_by_whole_ids() -> None:
    ids = " ".join(f"<id{i:03d}-{'y' * 30}@mail.example.test>" for i in range(100))
    thread = {
        "id": "t1",
        "messages": [
            _message(
                "m1",
                "a@customer.test",
                date="Mon, 26 Oct 2026 09:00:00 +0000",
                extra={"References": ids},
            )
        ],
    }
    fetched = await _client(lambda r: httpx.Response(200, json=thread)).fetch_thread(
        ACCESS, "t1", owner_address="owner@example.test"
    )
    assert fetched.latest_references is not None
    assert fetched.latest_references.startswith("<id") and fetched.latest_references.endswith(">")
    assert ids.endswith(fetched.latest_references)


def _draft_out(**overrides: Any) -> DraftOut:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "thread_id": "t1",
        "recipient": "a@example.test",
        "to": ["a@example.test"],
        "cc": [],
        "bcc": [],
        "recipients": [{"address": "a@example.test", "outside": False}],
        "subject": "Re: x",
        "body": "Hello",
        "status": "draft",
        "source": "human",
        "gmail_draft_id": None,
        "sent_at": None,
        "needs_staff_input": False,
        "reply_to_message_id": None,
        "created_at": "2026-10-29T01:00:00+00:00",
        "updated_at": "2026-10-29T01:00:00+00:00",
    }
    return DraftOut.model_validate({**fields, **overrides})


def test_draft_version_binds_thread_lists_subject_and_body() -> None:
    base = _draft_out()
    assert draft_version(base) == draft_version(_draft_out(status="sending", source="model"))
    assert len(draft_version(base)) == 64
    for change in (
        {"thread_id": None},
        {"to": ["b@example.test"]},
        {"cc": ["c@example.test"]},
        {"bcc": ["c@example.test"]},
        {"subject": "Re: y"},
        {"body": "Hello!"},
    ):
        assert draft_version(_draft_out(**change)) != draft_version(base), change


def test_recipient_refs_accept_employee_ids_or_plain_addresses() -> None:
    employee = uuid4()
    request = ComposeRequest(
        recipient_address="boss@example.test",
        cc=[str(employee), "ops@example.test"],
        bcc=["audit@example.test"],
        subject="s",
        instructions="i",
    )
    assert request.cc == [str(employee), "ops@example.test"]
    assert ReplyRequest(instructions="x").cc == [] and ReplyRequest().bcc == []
    for bad in (["Ops <ops@example.test>"], ["a@b"], ["x" * 250 + "@example.test"]):
        with pytest.raises(ValidationError):
            ReplyRequest(instructions="x", cc=bad)
    with pytest.raises(ValidationError):
        ReplyRequest(instructions="x", bcc=[f"p{i}@example.test" for i in range(21)])


def test_draft_update_takes_lists_and_keeps_single_recipient_form() -> None:
    legacy = DraftUpdate(recipient="a@example.test", subject="s", body="b")
    assert legacy.to == ["a@example.test"] and legacy.cc == [] and legacy.bcc == []
    lists = DraftUpdate(to=["a@example.test"], cc=["b@example.test"], subject="s", body="b")
    assert lists.cc == ["b@example.test"]
    with pytest.raises(ValidationError):
        DraftUpdate(subject="s", body="b")
    with pytest.raises(ValidationError):
        DraftUpdate(to=["a@example.test\r\nBcc: x@y.test"], subject="s", body="b")


def test_dedupe_keeps_first_occurrence_in_to_then_cc_then_bcc() -> None:
    to, cc, bcc = recipients.dedupe(
        ["A@example.test", "b@example.test"],
        ["a@example.test", "c@example.test", "B@EXAMPLE.test"],
        ["c@example.test", "d@example.test", "D@example.test"],
    )
    assert to == ["A@example.test", "b@example.test"]
    assert cc == ["c@example.test"] and bcc == ["d@example.test"]
