import re
from typing import Any

MAX_HEADLINE_LENGTH = 200


def parse_headline(value: Any, fallback_text: str) -> str:
    candidate: Any = value
    if isinstance(value, dict):
        candidate = value.get("headline")
    if isinstance(candidate, str):
        normalized = _single_line(candidate)
        if normalized:
            return normalized[:MAX_HEADLINE_LENGTH]
    return deterministic_headline(fallback_text)


def deterministic_headline(text: str) -> str:
    normalized = _single_line(text)
    return (normalized or "Empty user message")[:MAX_HEADLINE_LENGTH]


def _single_line(value: str) -> str:
    first_line = value.splitlines()[0] if value.splitlines() else ""
    return re.sub(r"\s+", " ", first_line).strip()
