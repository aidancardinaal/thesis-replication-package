"""
Shared LLM call-safety helpers.

Lives in its own module so both the agent and tool_selector can import it
without creating a circular import (the agent imports tool_selector).

The Azure OpenAI deployment used by the pipeline runs a content-moderation /
prompt-injection ("jailbreak") layer that occasionally produces a false
positive on a benign automation description. When it does, the provider
returns an HTTP 400 and the LangChain chain raises. Left uncaught, this kills
a whole participant run. `safe_invoke` retries transient failures and surfaces
a typed `ContentPolicyError` so callers can recover in-session instead of
crashing.
"""

from __future__ import annotations

import time
from typing import Any


class ContentPolicyError(Exception):
    """Raised when the provider's content / prompt-injection filter rejects a call."""


# Substrings that identify an Azure content-filter / responsible-AI / jailbreak
# rejection. Matched case-insensitively against the exception text and its cause.
_CONTENT_POLICY_MARKERS = (
    "content_filter",
    "content filter",
    "responsibleai",
    "responsible ai",
    "jailbreak",
    "content management policy",
    "content_policy",
)

# Error-type names that indicate a transient failure worth a plain retry.
_TRANSIENT_TYPE_MARKERS = (
    "ratelimit",
    "timeout",
    "apiconnection",
    "internalserver",
    "serviceunavailable",
    "apierror",
)


def _error_texts(exc: BaseException) -> str:
    """Collect the message text of an exception and its chained cause/context."""
    parts = []
    seen = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(f"{type(cur).__name__}: {cur}")
        # openai errors carry useful detail on .body / .code
        for attr in ("code", "body"):
            val = getattr(cur, attr, None)
            if val is not None:
                parts.append(str(val))
        cur = cur.__cause__ or cur.__context__
    return " | ".join(parts).lower()


def is_content_policy_error(exc: BaseException) -> bool:
    """True if `exc` (or a chained cause) looks like a content-filter rejection."""
    text = _error_texts(exc)
    return any(marker in text for marker in _CONTENT_POLICY_MARKERS)


def _is_transient_error(exc: BaseException) -> bool:
    type_names = " ".join(
        type(c).__name__.lower()
        for c in (exc, exc.__cause__, exc.__context__)
        if c is not None
    )
    return any(marker in type_names for marker in _TRANSIENT_TYPE_MARKERS)


def safe_invoke(chain: Any, payload: dict, *, retries: int = 2, backoff: float = 1.0) -> Any:
    """
    Invoke a LangChain runnable with bounded retries.

    - Transient errors (rate limit, timeout, transient API) are retried.
    - Content-filter rejections are retried (the filter is occasionally
      non-deterministic) and, if they persist, re-raised as `ContentPolicyError`
      so the caller can recover in-session.
    - Any other exception is re-raised unchanged.
    """
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return chain.invoke(payload)
        except Exception as exc:  # noqa: BLE001 - we re-raise below
            last_exc = exc
            content_policy = is_content_policy_error(exc)
            transient = _is_transient_error(exc)
            if not (content_policy or transient):
                raise
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            if content_policy:
                raise ContentPolicyError(str(exc)) from exc
            raise
    # Unreachable, but keeps type checkers happy.
    raise last_exc  # type: ignore[misc]
