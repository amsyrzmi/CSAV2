"""Pure contract tests for the staff email assistant: parsing, bounds, PKCE, settings."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from email import message_from_bytes
from typing import Any
from uuid import uuid4

import httpx
import pytest
from csa.email import gmail, recipients
from csa.email.models import DraftProposal, ThreadAnalysis, validated_draft
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


def test_model_outputs_are_strict_and_bounded() -> None:
    with pytest.raises(ValidationError):
        ThreadAnalysis.model_validate(
            {
                "category": "complaint",
                "priority": "high",
                "language": "en",
                "summary": "s",
                "tool_call": "x",
            }
        )
    with pytest.raises(ValidationError):
        ThreadAnalysis.model_validate(
            {"category": "complaint", "priority": "urgent", "language": "en", "summary": "s"}
        )
    with pytest.raises(ValidationError):
        ThreadAnalysis.model_validate(
            {
                "category": "complaint",
                "priority": "high",
                "language": "en",
                "summary": "s",
                "action_items": ["x" * 201],
            }
        )
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


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
async def test_send_message_4xx_is_send_refused(status: int) -> None:
    with pytest.raises(gmail.GmailError) as err:
        await _client(lambda r: httpx.Response(status, json={"error": "no"})).send_message(
            ACCESS, thread_id=None, mime=b"x"
        )
    assert err.value.code == "send_refused"


async def test_send_message_5xx_is_ambiguous_not_refused() -> None:
    with pytest.raises(gmail.GmailError) as err:
        await _client(lambda r: httpx.Response(503)).send_message(ACCESS, thread_id=None, mime=b"x")
    assert err.value.code == "provider_unreachable"


async def test_list_thread_ids_passes_page_token_and_returns_next() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"threads": [{"id": "a"}, {"id": "b"}], "nextPageToken": "tok-2"}
        )

    ids, next_token = await _client(handler).list_thread_ids(
        ACCESS, query="newer_than:90d", limit=20, page_token="tok-1"
    )
    assert ids == ["a", "b"] and next_token == "tok-2"
    assert seen[0].url.params["pageToken"] == "tok-1"
    assert seen[0].url.params["maxResults"] == "20"


async def test_list_thread_ids_last_page_has_no_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"threads": [{"id": "a"}]})

    ids, next_token = await _client(handler).list_thread_ids(ACCESS, query="q", limit=5)
    assert ids == ["a"] and next_token is None
    assert "pageToken" not in seen[0].url.params


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
