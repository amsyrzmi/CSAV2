"""Capacity-gate tooling: the model stub must satisfy the real client and the real schemas."""

from __future__ import annotations

import importlib.util
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from csa.decide.groq import GroqClient
from csa.decide.llm import Classification, ComposeOut, PlanOut, ReviewOut, StepOut

ROOT = Path(__file__).resolve().parents[1]
CAPACITY = ROOT / "deploy" / "capacity"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, CAPACITY / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stub = _load("model_stub")
SCHEMAS = {
    "classify": Classification,
    "plan": PlanOut,
    "step": StepOut,
    "compose": ComposeOut,
    "review": ReviewOut,
}


def test_every_prompt_opening_the_stub_recognises_is_still_in_the_provider() -> None:
    source = (ROOT / "csa" / "decide" / "llm.py").read_text(encoding="utf-8")
    for opening, _kind in stub.KINDS:
        assert opening in source, opening


def test_every_stub_answer_validates_against_the_real_schema() -> None:
    assert set(stub.ANSWERS) == set(SCHEMAS) == {kind for _, kind in stub.KINDS}
    for kind, schema in SCHEMAS.items():
        schema.model_validate(stub.ANSWERS[kind])


@pytest.fixture()
def stub_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setattr(stub, "DELAY", dict.fromkeys(stub.DELAY, 0.0))
    stub.COUNTS.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), stub.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


async def test_the_real_groq_client_gets_a_valid_answer_for_each_call(stub_url: str) -> None:
    openings = {kind: opening for opening, kind in stub.KINDS}
    async with httpx.AsyncClient() as http:
        client = GroqClient("synthetic", "synthetic-model", http, f"{stub_url}/v1/chat/completions")
        for kind, schema in SCHEMAS.items():
            result = await client.json_completion(
                schema, system=f"{openings[kind]} for a test.", user="hello", timeout=5.0
            )
            assert isinstance(result, schema)
        assert httpx.get(stub_url).json() == dict.fromkeys(SCHEMAS, 1)


async def test_an_unknown_call_is_refused_not_guessed(stub_url: str) -> None:
    async with httpx.AsyncClient() as http:
        response = await http.post(
            f"{stub_url}/v1/chat/completions",
            json={"messages": [{"role": "system", "content": "Something else entirely"}]},
        )
    assert response.status_code == 400
