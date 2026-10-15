"""Embedder clients for the Knowledge Base agent (LG13).

Vectors are 384-dimensional (BAAI/bge-small-en-v1.5 class models served by TEI).
``TEI_ENDPOINT`` selects the TEI embedder; TEI failures raise
:class:`KbEmbeddingError` instead of falling back, so one index never mixes
vectors from different models. Without TEI, dev/ci use a deterministic hashing
embedder labelled ``hash-384``; prod refuses to run without TEI.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING, Protocol

import httpx
import structlog

if TYPE_CHECKING:
    from csa.core.settings import Settings

DIMENSIONS = 384
HASH_MODEL = "hash-384"
TEI_MODEL = "tei-384"

logger = structlog.get_logger(__name__)


class KbEmbeddingError(RuntimeError):
    """Raised when embeddings cannot be produced; never silently degraded."""


class KbEmbedder(Protocol):
    """Protocol for vector embedding generation."""

    @property
    def model_name(self) -> str: ...
    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


def _deterministic_embedding(text: str, dim: int = DIMENSIONS) -> list[float]:
    """Generate a deterministic, normalized 384-dim vector from text.

    Uses bag-of-words and character n-gram hashing so overlapping terms land
    near each other. It is lexical, not semantic; use TEI for real retrieval.
    """
    cleaned = re.sub(r"[^\w\s]", "", text.lower()).strip()
    words = cleaned.split()
    vec = [0.0] * dim

    if not words:
        vec[0] = 1.0
        return vec

    for w in words:
        h_val = int(hashlib.sha256(w.encode("utf-8")).hexdigest(), 16)
        idx = h_val % dim
        sign = 1.0 if ((h_val >> 8) & 1) == 0 else -1.0
        vec[idx] += 2.0 * sign

        for i in range(max(1, len(w) - 2)):
            ng = w[i : i + 3]
            ng_h = int(hashlib.md5(ng.encode("utf-8")).hexdigest(), 16)
            idx_ng = ng_h % dim
            sign_ng = 1.0 if ((ng_h >> 8) & 1) == 0 else -1.0
            vec[idx_ng] += 0.5 * sign_ng

    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-9:
        vec[0] = 1.0
        return vec

    return [x / norm for x in vec]


def vector_to_sql(vec: list[float]) -> str:
    """Format a float vector as a string suitable for PostgreSQL pgvector casting."""
    return f"[{','.join(f'{x:.6f}' for x in vec)}]"


class DefaultKbEmbedder:
    """TEI embedder when ``endpoint`` is set, otherwise the deterministic hash embedder."""

    # TEI accepts a bounded batch per request; larger documents are sent in slices.
    BATCH_SIZE = 32

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        model_label: str = TEI_MODEL,
        query_prefix: str = "",
        passage_prefix: str = "",
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/") if endpoint else None
        self._model_name = model_label if self.endpoint else HASH_MODEL
        self._query_prefix = query_prefix
        self._passage_prefix = passage_prefix
        self._http = http_client
        self._timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> DefaultKbEmbedder:
        if settings.TEI_ENDPOINT is not None:
            return cls(
                str(settings.TEI_ENDPOINT),
                model_label=settings.KB_EMBED_MODEL_LABEL,
                query_prefix=settings.TEI_QUERY_PREFIX,
                passage_prefix=settings.TEI_PASSAGE_PREFIX,
            )
        if settings.ENV == "prod":
            raise KbEmbeddingError("TEI_ENDPOINT is required for the knowledge base in prod")
        logger.warning("kb_embedder_hash_mode", reason="TEI_ENDPOINT not configured")
        return cls()

    @property
    def model_name(self) -> str:
        return self._model_name

    async def embed(self, text: str) -> list[float]:
        """Embed a search query."""
        if not self.endpoint:
            return _deterministic_embedding(text)
        return (await self._tei_embed([self._query_prefix + text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed document passages for storage."""
        if not texts:
            return []
        if not self.endpoint:
            return [_deterministic_embedding(t) for t in texts]

        out: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[start : start + self.BATCH_SIZE]
            out.extend(await self._tei_embed([self._passage_prefix + t for t in batch]))
        return out

    async def _tei_embed(self, texts: list[str]) -> list[list[float]]:
        assert self.endpoint is not None
        client = self._http or httpx.AsyncClient(timeout=self._timeout)
        try:
            resp = await client.post(
                f"{self.endpoint}/embed",
                json={"inputs": texts, "truncate": True},
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise KbEmbeddingError(f"TEI request failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if self._http is None:
                await client.aclose()

        if not isinstance(data, list) or len(data) != len(texts):
            raise KbEmbeddingError("TEI returned an unexpected number of embeddings")
        for vec in data:
            if not isinstance(vec, list) or len(vec) != DIMENSIONS:
                raise KbEmbeddingError(f"TEI embedding is not {DIMENSIONS}-dimensional")
        return [[float(x) for x in vec] for vec in data]
