"""Redaction helpers for production records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


SECRET_MARKERS = ("authorization", "credential", "password", "secret", "token", "api_key", "apikey")
REDACTED = "[REDACTED]"


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_MARKERS)


def redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): (REDACTED if is_secret_key(str(key)) else redact_value(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    return value


def redact_text(text: str) -> str:
    words = []
    for word in text.split():
        lowered = word.lower()
        if any(marker in lowered for marker in ("secret", "token", "password", "credential")):
            words.append(REDACTED)
        else:
            words.append(word)
    return " ".join(words)


def redact_headers(headers: Mapping[str, str] | Sequence[tuple[str, str]]) -> dict[str, str]:
    items = headers.items() if isinstance(headers, Mapping) else headers
    return {str(key): (REDACTED if is_secret_key(str(key)) else str(value)) for key, value in items}
