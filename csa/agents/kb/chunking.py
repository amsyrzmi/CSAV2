"""Text chunking for knowledge base ingestion (LG13)."""

from __future__ import annotations

import re

DEFAULT_MAX_CHARS = 600
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _split_long(piece: str, max_chars: int) -> list[str]:
    """Split one oversized paragraph on sentence boundaries, then on words."""
    out: list[str] = []
    cur = ""
    for sentence in (s.strip() for s in _SENTENCE_SPLIT.split(piece)):
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if cur:
                out.append(cur)
                cur = ""
            words = sentence.split()
            window = ""
            for word in words:
                candidate = f"{window} {word}".strip()
                if len(candidate) > max_chars and window:
                    out.append(window)
                    window = word[:max_chars]
                else:
                    window = candidate[:max_chars]
            if window:
                out.append(window)
            continue
        candidate = f"{cur} {sentence}".strip()
        if len(candidate) > max_chars and cur:
            out.append(cur)
            cur = sentence
        else:
            cur = candidate
    if cur:
        out.append(cur)
    return out


def chunk_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Split text into chunks of at most ``max_chars``, keeping paragraphs together.

    Small adjacent paragraphs are merged so PDF line fragments do not become
    one-line chunks; oversized paragraphs are split by sentence, then by word.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for para in paragraphs:
        if len(para) > max_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.extend(_split_long(para, max_chars))
            continue
        candidate = f"{cur}\n\n{para}" if cur else para
        if len(candidate) > max_chars:
            chunks.append(cur)
            cur = para
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks
