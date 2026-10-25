"""Pure contract tests for the staff email assistant: parsing, bounds, PKCE, settings."""

from __future__ import annotations

import base64
from email import message_from_bytes
from uuid import uuid4

import pytest
from csa.email import gmail
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
