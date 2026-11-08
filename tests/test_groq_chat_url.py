"""The model endpoint is configurable for the production capacity test, and safe by default."""

from __future__ import annotations

import base64

import httpx
import pytest
from csa.core.settings import Settings
from csa.decide.groq import GROQ_URL, GroqClient
from pydantic import BaseModel, ValidationError

# Settings looks for /run/secrets even when told not to; the folder is absent off Docker.
pytestmark = pytest.mark.filterwarnings("ignore:directory .* does not exist:UserWarning")


class Answer(BaseModel):
    text: str


def make_settings(**overrides: str) -> Settings:
    env = {
        "ENV": "ci",
        "DATABASE_URL": "postgresql://csa_app:pw@localhost:5433/csa",
        "LG_DATABASE_URL": "postgresql://csa_app:pw@localhost:5433/csa",
        "GROQ_API_KEY": "synthetic",
        "PUBLIC_BASE_URL": "https://example.test",
        "CREDENTIALS_KEY": "base64:" + base64.b64encode(b"\x01" * 32).decode("ascii"),
    }
    env.update(overrides)
    return Settings(_env_file=None, _secrets_dir=None, **env)  # type: ignore[call-arg]


async def post_once(client_url: str | None) -> str:
    """Make one completion and return the URL the client posted to."""
    seen: list[str] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        content = '{"text": "ok"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    client = (
        GroqClient("k", "m", http) if client_url is None else GroqClient("k", "m", http, client_url)
    )
    await client.json_completion(Answer, system="s", user="u", timeout=5)
    return seen[0]


async def test_the_client_posts_to_groq_unless_told_otherwise() -> None:
    assert await post_once(None) == GROQ_URL


async def test_the_client_posts_to_the_configured_url() -> None:
    assert await post_once("http://model-stub:8080/v1/chat/completions") == (
        "http://model-stub:8080/v1/chat/completions"
    )


def test_the_default_setting_is_the_groq_endpoint() -> None:
    assert str(make_settings().GROQ_CHAT_URL) == GROQ_URL


@pytest.mark.parametrize(
    "url",
    [
        "https://models.example.com/v1/chat/completions",
        "http://model-stub:8080/v1/chat/completions",
    ],
)
def test_https_or_an_internal_single_label_host_is_accepted(url: str) -> None:
    assert str(make_settings(GROQ_CHAT_URL=url).GROQ_CHAT_URL) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://models.example.com/v1/chat/completions",
        "http://10.0.0.5:8080/v1/chat/completions",
        "http://127.0.0.1:8080/v1/chat/completions",
        "ftp://model-stub/v1/chat/completions",
    ],
)
def test_plain_http_to_a_dotted_host_or_address_is_refused(url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(GROQ_CHAT_URL=url)
