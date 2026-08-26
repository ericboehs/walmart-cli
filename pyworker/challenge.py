"""Recognizing a bot-protection block in a response.

The search capture returned a 412 whose body was a PerimeterX block payload:
appId, blockScript, firstPartyEnabled, hostUrl, jsClientSrc, a redirectUrl to
/blocked, and a visitor id. That matters because a status code alone is not a
diagnosis. Walmart returns 412 for ordinary precondition failures too -- the
first capture saw one on setFulfillmentIntent -- so the body decides, not the
number.

We do not solve, simulate, evade, or retry a block. A blocked request is handed
back to the human with an explanation, and the command stops. Retrying a block
is how an account gets a longer one.
"""

from __future__ import annotations

from typing import Any

# Keys that only a block payload carries. Names are matched case-insensitively
# and without separators, so blockScript, block_script, and BlockScript all
# count as the same evidence.
BLOCK_KEYS = {
    "blockscript",
    "jsclientsrc",
    "firstpartyenabled",
}

# Strings that mean "an interstitial, not an answer", wherever they appear in
# the payload's own URLs.
BLOCK_URL_MARKERS = ("/blocked", "px-cloud.net", "perimeterx", "captcha")

# How many block-only keys must be present before we call it a block. Two,
# because a single generic key could plausibly show up in a real response and
# mislabelling a working reply as a block would strand the user.
BLOCK_KEY_THRESHOLD = 2

CHALLENGE_CODE = "challenge_detected"

ADVICE = (
    "Walmart's bot protection blocked this request. "
    "Run `walmart login` to open a visible window and clear the challenge yourself, then try again. "
    "This tool will not retry automatically."
)


class Blocked(Exception):
    """A request was refused by bot protection.

    Carries the worker error code so the CLI reports a challenge rather than a
    generic failure, and so nothing downstream is tempted to retry.
    """

    code = CHALLENGE_CODE

    def __init__(self, message: str = ADVICE, *, detail: str | None = None):
        super().__init__(message)
        self.detail = detail


def _normalize(key: str) -> str:
    return "".join(character for character in str(key).lower() if character.isalnum())


def detect(payload: Any) -> str | None:
    """Name the block vendor if this payload is one, else None.

    Only the top level is examined. A block payload *is* the response; a real
    response that happens to mention a captcha somewhere deep inside is not the
    same thing, and treating it as one would break search on a bad day.
    """
    if not isinstance(payload, dict):
        return None

    keys = {_normalize(key) for key in payload}
    if len(keys & BLOCK_KEYS) >= BLOCK_KEY_THRESHOLD:
        return "perimeterx" if "appid" in keys or "pxappid" in keys else "unknown"

    for key in ("redirecturl", "blockscript", "hosturl"):
        value = _value_for(payload, key)
        if isinstance(value, str) and any(marker in value.lower() for marker in BLOCK_URL_MARKERS):
            return "perimeterx" if "px" in value.lower() else "unknown"
    return None


def _value_for(payload: dict, normalized_key: str) -> Any:
    for key, value in payload.items():
        if _normalize(key) == normalized_key:
            return value
    return None


def detect_url(url: str | None) -> str | None:
    """Name the block vendor if this is where a block sends you, else None.

    A navigation that lands on the interstitial is a block even when the body
    is ordinary HTML. URL beats page text: the text is localized and
    redesigned, the destination is not.
    """
    if not url:
        return None
    lowered = str(url).lower()
    if not any(marker in lowered for marker in BLOCK_URL_MARKERS):
        return None
    return "perimeterx" if "px" in lowered or "perimeterx" in lowered else "unknown"


def raise_if_blocked_url(url: str | None) -> None:
    """Stop the command when a navigation landed on a block. Never retries."""
    vendor = detect_url(url)
    if vendor is None:
        return
    raise Blocked(ADVICE, detail=f"navigation landed on a block page ({vendor})")


def raise_if_blocked(payload: Any, *, status: int | None = None) -> None:
    """Stop the command when a response is a block. Never retries.

    The status is reported when we have it, because "412 that was actually a
    block" is the sentence that saves the next person an afternoon.
    """
    vendor = detect(payload)
    if vendor is None:
        return
    where = f"HTTP {status}: " if status is not None else ""
    raise Blocked(f"{where}{ADVICE}", detail=f"block payload from {vendor}")
