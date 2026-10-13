"""Shared JSON-completion provider contract and secret-free provider errors."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class CompletionError(Exception):
    """Provider failure safe to log without request or credential details."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class JsonCompletionClient(Protocol):
    model: str

    async def json_completion(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        timeout: float,
        max_tokens: int = 1500,
    ) -> T: ...
