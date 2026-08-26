"""The item action: navigate to an item page and read what it shipped."""

from __future__ import annotations

import re
from typing import Any

import browser
import challenge
import item_parse
import protocol

ITEM_URL = f"{browser.WALMART_ORIGIN}/ip"

# A Walmart item id is digits. Anything else is either a URL we can read an id
# out of, or a mistake worth reporting before a browser opens.
ITEM_ID = re.compile(r"^\d{4,}$")
ID_IN_URL = re.compile(r"/ip/(?:[^/]+/)?(\d{4,})")

NOT_AN_ITEM = (
    "expected a Walmart item id or an item URL, for example 10450114 or "
    "https://www.walmart.com/ip/great-value-whole-milk/10450114"
)

NO_PAYLOAD = (
    "the item page did not ship its product data; Walmart may have changed the page, "
    "or the session may need `walmart login`"
)

NEXT_DATA_SCRIPT = """
() => {
  const node = document.getElementById('__NEXT_DATA__');
  if (!node) { return null; }
  try { return JSON.parse(node.textContent); } catch (error) { return null; }
}
"""


def normalize_params(request: dict[str, Any]) -> dict[str, Any]:
    params = request.get("params") or {}
    raw = str(params.get("item") or params.get("item_id") or "").strip()
    if not raw:
        raise ValueError(NOT_AN_ITEM)

    return {"item_id": item_id_from(raw)}


def item_id_from(text: str) -> str:
    """The item id in whatever the user pasted.

    Accepting a URL matters because a URL is what a browser gives you, and
    making someone dig the number out by hand is a papercut we can just not
    have.
    """
    candidate = text.strip()
    if ITEM_ID.match(candidate):
        return candidate

    match = ID_IN_URL.search(candidate)
    if match:
        return match.group(1)
    raise ValueError(NOT_AN_ITEM)


def item_url(item_id: str) -> str:
    return f"{ITEM_URL}/{item_id}"


def action_item(request: dict[str, Any]) -> dict[str, Any]:
    """Worker entry point for `walmart item`."""
    params = normalize_params(request)
    options = browser.BrowserOptions.from_request(request.get("options", {}))

    with browser.open_session(options) as session:
        protocol.progress(f"reading item {params['item_id']}")
        return read_item(session, params["item_id"])


def read_item(session, item_id: str) -> dict[str, Any]:
    response = session.goto(item_url(item_id), wait_until="domcontentloaded")
    status = getattr(response, "status", None) if response is not None else None

    if status == 429:
        raise RateLimited("Walmart is rate-limiting this session; wait a few minutes and try again")
    challenge.raise_if_blocked_url(getattr(session.page, "url", None))
    if status == 404:
        raise ItemNotFound(f"Walmart has no item {item_id}")

    payload = session.page.evaluate(NEXT_DATA_SCRIPT)
    if not payload:
        raise RuntimeError(NO_PAYLOAD)

    return item_parse.parse_item(payload)


class ItemNotFound(Exception):
    """The id is well-formed and Walmart does not have it."""

    code = "not_found"


class RateLimited(Exception):
    """Throttled. Distinct from a block, and never retried automatically."""

    code = "rate_limited"
