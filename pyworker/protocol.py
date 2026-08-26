"""NDJSON protocol between the Ruby CLI and this worker.

stdout carries protocol events only, one JSON object per line. Anything a human
should read goes to stderr, where Ruby keeps a bounded tail for diagnostics.

Secrets arrive on stdin inside the request and must never be echoed: emit() and
redact() exist so a careless debug line cannot leak one.
"""

from __future__ import annotations

import json
import sys
from typing import Any

PROTOCOL_VERSION = 1

SECRET_KEYS = {"password", "email", "secrets", "otp", "code", "token", "cookie"}


def emit(event: dict[str, Any], stream=None) -> None:
    """Write one protocol event."""
    stream = stream if stream is not None else sys.stdout
    stream.write(json.dumps(event, separators=(",", ":"), default=str) + "\n")
    stream.flush()


def log(message: str, level: str = "info", stream=None) -> None:
    emit({"type": "log", "level": level, "message": message}, stream)


def progress(message: str, current: int | None = None, total: int | None = None, stream=None) -> None:
    event: dict[str, Any] = {"type": "progress", "message": message}
    if current is not None:
        event["current"] = current
    if total is not None:
        event["total"] = total
    emit(event, stream)


def result(data: dict[str, Any], stream=None) -> None:
    """Terminal success payload.

    Results are redacted too, not just errors. A parser that accidentally lifts
    an email or a token out of a page should not be able to launder it into the
    CLI's public JSON just because the run succeeded.
    """
    emit({"type": "result", "data": redact(data)}, stream)


def error(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    unverified: bool = False,
    stream=None,
) -> None:
    """Terminal failure.

    ``unverified=True`` means a mutation may have happened but we could not
    confirm it; Ruby turns that into exit code 3 rather than a false failure.
    """
    event: dict[str, Any] = {"type": "error", "code": code, "message": message}
    if details:
        event["details"] = redact(details)
    if unverified:
        event["unverified"] = True
    emit(event, stream)


def redact(value: Any) -> Any:
    """Deep-copy a structure with anything secret-shaped replaced."""
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if str(key).lower() in SECRET_KEYS else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def read_request(stream=None) -> dict[str, Any]:
    """Read the single request object from stdin."""
    stream = stream if stream is not None else sys.stdin
    raw = stream.readline()
    if not raw.strip():
        raise ValueError("empty request on stdin")
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    if "action" not in request:
        raise ValueError("request is missing 'action'")

    # A version mismatch means the Ruby and Python halves were installed from
    # different revisions. Say so plainly instead of failing later in a way that
    # looks like Walmart changed something.
    version = request.get("protocol", PROTOCOL_VERSION)
    if version != PROTOCOL_VERSION:
        raise ValueError(
            f"unsupported protocol version {version!r}; this worker speaks {PROTOCOL_VERSION}. "
            "The Ruby CLI and pyworker are probably out of sync."
        )
    return request
