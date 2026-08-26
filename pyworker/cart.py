"""Reading the live cart.

The cart is read by loading the cart page and keeping the getCart response the
page fetches for itself. That is deliberate, and it is the second time the same
lesson has come up: a captured cart URL replayed with only the stable operation
headers earns a 429, while the page's own identical request returns 200. So we
do not replay. We navigate, listen, and read what arrived.

It also sidesteps the cart id entirely. The id is a session-scoped identifier
that would otherwise have to be found, carried, and kept out of logs; by letting
the page ask its own question we never hold it at all.
"""

from __future__ import annotations

import json
from typing import Any

import browser
import cart_parse
import challenge
import protocol

CART_URL = f"{browser.WALMART_ORIGIN}/cart"

# The operation whose response is the cart. Matched on the path, since the hash
# that follows changes with every web build.
GETCART_MARKER = "/orchestra/home/graphql/getcart/"

# How long to wait for the page to ask for its own cart, and how finely to
# slice that wait. The sync Playwright API runs response handlers only while
# something is pumping, so this loop is not a sleep -- it is the thing that
# lets the handler fire at all.
WAIT_SECONDS = 30
SLICE_MS = 250

NO_CART_RESPONSE = (
    "the cart page never fetched its cart; Walmart may have changed the page, "
    "or the session may need `walmart login`"
)


# One name for one condition, whether the cart never arrived or arrived
# unreadable. Both mean "I cannot tell you what is in your cart", which is a
# different sentence from "your cart is empty".
CartUnreadable = cart_parse.CartUnreadable


def action_cart_show(request: dict[str, Any]) -> dict[str, Any]:
    """Worker entry point for `walmart cart show`."""
    options = browser.BrowserOptions.from_request(request.get("options", {}))
    wait_seconds = _wait_seconds(request)

    with browser.open_session(options) as session:
        protocol.progress("reading the cart")
        payload = capture_cart(session, wait_seconds=wait_seconds)

    return cart_parse.parse_cart(payload)


def capture_cart(session, wait_seconds: int = WAIT_SECONDS) -> Any:
    """Load the cart page and return the cart payload the page fetched."""
    captured: list[Any] = []
    session.on_response(_collector(captured))

    response = session.goto(CART_URL, wait_until="domcontentloaded")
    status = getattr(response, "status", None) if response is not None else None
    if status == 429:
        raise RuntimeError("Walmart is rate-limiting this session; wait a few minutes")

    challenge.raise_if_blocked_url(getattr(session.page, "url", None))

    payload = _pump_until_captured(session, captured, wait_seconds)
    if payload is None:
        raise CartUnreadable(NO_CART_RESPONSE)

    # A block can arrive as the cart response itself, in which case an empty
    # cart is exactly what a naive parse would report.
    challenge.raise_if_blocked(payload)
    return payload


def _collector(captured: list[Any]):
    def handle(response) -> None:
        url = (getattr(response, "url", "") or "").lower()
        if GETCART_MARKER not in url:
            return
        try:
            captured.append(response.json())
        except Exception:  # noqa: BLE001 - one unreadable response is not fatal
            return

    return handle


def _pump_until_captured(session, captured: list[Any], wait_seconds: int) -> Any:
    deadline_slices = max(1, int(wait_seconds * 1000 / SLICE_MS))
    for _ in range(deadline_slices):
        if captured:
            # The last one wins: the page may fetch the cart more than once,
            # and the newest read is the one that reflects the cart now.
            return captured[-1]
        session.page.wait_for_timeout(SLICE_MS)
    return captured[-1] if captured else None


def _wait_seconds(request: dict[str, Any]) -> int:
    params = request.get("params") or {}
    raw = params.get("wait_seconds")
    if raw is None:
        return WAIT_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected a whole number of seconds, got {raw!r}") from exc
    if value < 0:
        raise ValueError("a negative wait is not a wait")
    return value


def load_payload(raw: str) -> Any:
    """Decode a cart payload from text. Used by tests and by the recon tooling."""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None
