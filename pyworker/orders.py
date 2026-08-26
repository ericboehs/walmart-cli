"""Walking purchase history.

The walk is deliberately boring: load /orders, read the rows the page shipped,
load each order's own page for the record, and click "Next page" for more.
Nothing here synthesizes a request. The cursor exists, and we could build the
PurchaseHistoryV3 call ourselves, but a replayed history request with a partial
header set is exactly the shape that earned a 429 during recon -- and unlike a
search, a throttled history walk fails halfway through somebody's two years.

The walk is also interruptible by design. A block or a throttle stops it, and
what it collected so far is returned along with the reason and the cursor it
stopped at. A delivered partial result beats a wedged exit: the archive can
store what arrived, mark itself incomplete, and resume. The one thing this must
never do is finish quietly and let the caller believe it saw everything.
"""

from __future__ import annotations

import re
from typing import Any

import browser
import challenge
import orders_parse
import protocol

ORDERS_URL = f"{browser.WALMART_ORIGIN}/orders"

# The paged history response, matched on path: the hash changes per web build.
HISTORY_MARKER = "/orchestra/cph/graphql/purchasehistoryv3/"

NEXT_PAGE_LABELS = ("Next page", "Next")

# An order id is Walmart's data, not ours. archive.rb already treats it as
# untrusted before it becomes a filesystem path; this is the same value about
# to become a URL, and the same provenance deserves the same suspicion.
# Anything outside this set could otherwise carry an encoded traversal into a
# destination this tool must never reach.
ORDER_ID = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z")

# Walmart's own page size. Raising it hopefully is an experiment with a 429 as
# the price of being wrong, so it stays where the site put it.
PAGE_SIZE = 5

# A ceiling on pages, not a target. Two years at five orders a page is a lot of
# navigation, and an unbounded loop against someone else's site is how a bug
# becomes abuse.
#
# This comment was true and the code was not: the value reached normalize_params
# as _bounded(request_value, MAX_PAGES), a default, so any number the request
# named was honoured. "MAX" in a name is a claim; the mechanism underneath it is
# a separate fact, and the two read as agreeing until you ask who supplies the
# value. A limit the caller can raise is not a limit -- and the worker is a
# separate process precisely so it does not have to trust its caller. It clamps.
MAX_PAGES = 200

SLICE_MS = 250
PAGE_WAIT_SECONDS = 20

# Why a walk stopped. COMPLETE is the only one that means nothing is missing;
# CUTOFF means nothing is missing *within the window we asked for*. Everything
# else leaves history unseen, and the archive has to say so.
COMPLETE = "complete"
CUTOFF = "cutoff"
PAGE_LIMIT = "page_limit"
# The cursor promised another page and the control to reach it was not there.
# That is not the same as reaching the end, so it is not COMPLETE.
NO_NEXT_PAGE = "no_next_page"


class Stop(Exception):
    """Stop walking, keep what we have. Carries the reason for the archive."""

    def __init__(self, reason: str, message: str, cursor: str | None = None):
        super().__init__(message)
        self.reason = reason
        self.cursor = cursor


def action_order_sync(request: dict[str, Any]) -> dict[str, Any]:
    """Worker entry point for `walmart order sync`.

    Returns every order record it managed to read, plus why it stopped. Both
    halves matter: the caller archives the records and stores the reason so a
    later run knows whether it is resuming or starting fresh.
    """
    params = normalize_params(request)
    options = browser.BrowserOptions.from_request(request.get("options", {}))

    with browser.open_session(options) as session:
        return walk(session, params)


def normalize_params(request: dict[str, Any]) -> dict[str, Any]:
    params = request.get("params") or {}
    known = params.get("known_ids") or []
    if not isinstance(known, list):
        raise ValueError("known_ids must be a list of order ids")

    return {
        # ISO date. Orders older than this end the walk; None means all history.
        "since": _string(params.get("since")),
        "full": bool(params.get("full")),
        # Ids the caller already has. Their detail pages are not fetched, which
        # is what makes a re-sync cheap.
        "known_ids": {str(value) for value in known},
        "max_pages": _bounded(params.get("max_pages"), MAX_PAGES, maximum=MAX_PAGES),
        "wait_seconds": _bounded(params.get("wait_seconds"), PAGE_WAIT_SECONDS),
    }


def _bounded(value: Any, default: int, maximum: int | None = None) -> int:
    """A count from the request, clamped to something defensible.

    MAX_PAGES is described as a ceiling, and it was only a default: the request
    could name any number and get it. Our own CLI never sends max_pages, so
    nothing was wrong in practice -- but the worker is a separate process and
    its input contract should not depend on the only current caller being
    well-behaved. Clamping rather than raising, because a ceiling that refuses
    the request instead of honouring it as far as it can is a worse ceiling.
    """
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected a whole number, got {value!r}") from exc
    if number < 1:
        raise ValueError(f"{number} is not a usable count")
    return min(number, maximum) if maximum is not None else number


def walk(session, params: dict[str, Any]) -> dict[str, Any]:
    """Page through history, reading each new order. Never raises for a block."""
    records: list[dict[str, Any]] = []
    seen: list[dict[str, Any]] = []
    cursor: str | None = None
    reason = COMPLETE

    try:
        page = _first_page(session)
        for number in range(1, params["max_pages"] + 1):
            cursor = page.get("next_cursor")
            seen.extend(page["orders"])
            _read_page(session, page["orders"], params, records)

            if not cursor:
                # No cursor means Walmart says there is nothing older. That is
                # the one honest "complete".
                reason = COMPLETE
                break
            if number >= params["max_pages"]:
                reason = PAGE_LIMIT
                break

            protocol.progress(f"reading history page {number + 1}")
            page = _next_page(session, params)
    except Stop as stop:
        reason = stop.reason
        cursor = stop.cursor or cursor
        protocol.progress(str(stop))

    return {
        "orders": records,
        "seen": seen,
        "stopped": {
            "reason": reason,
            # No cursor crosses this boundary. Ruby never replayed it -- the
            # walk clicks Next, because replaying a cursor earned a 429 -- so
            # sending one only invited it to be written down, which it was, for
            # a resume that does not exist. An identifier with no consumer is a
            # liability and nothing else.
            "oldest_date": _oldest_date(seen),
        },
    }


def _oldest_date(seen: list[dict[str, Any]]) -> str | None:
    """The oldest date actually read, taken as a minimum rather than as the last row.

    Walmart returns history newest first and the cutoff logic depends on that,
    but this value is a *claim about coverage* -- the archive prints "nothing
    older than X" from it. Deriving a claim from an assumption about ordering
    means one out-of-order page turns it into a confident lie, so it is
    computed from the dates themselves.
    """
    dates = [row["date"] for row in seen if row.get("date")]
    return min(dates) if dates else None


def _first_page(session) -> dict[str, Any]:
    """The server-rendered first page. One navigation, no synthesized request."""
    response = session.goto(ORDERS_URL, wait_until="domcontentloaded")
    _guard(session, response)
    payload = session.page.evaluate(_NEXT_DATA_SCRIPT)
    if not payload:
        raise Stop(
            "history_unreadable",
            "the orders page did not ship its history; the session may need `walmart login`",
        )
    return orders_parse.parse_history(payload)


def _next_page(session, params: dict[str, Any]) -> dict[str, Any]:
    """Click through to the next page and read the response it triggers.

    Clicking is how a person pages, and it means the request carries the
    session's own headers rather than ours.
    """
    captured: list[Any] = []
    session.on_response(_collector(captured))

    button = _next_button(session)
    if button is None:
        raise Stop(NO_NEXT_PAGE, "no next-page control on this page")
    session.click(button, label="Next page")

    payload = _pump_until(session, captured, params["wait_seconds"])
    if payload is None:
        raise Stop("history_unreadable", "the next page of history never arrived")

    challenge.raise_if_blocked(payload)
    return orders_parse.parse_history(payload)


def _next_button(session):
    for label in NEXT_PAGE_LABELS:
        locator = session.page.get_by_role("button", name=label)
        if locator.count() > 0 and locator.is_enabled():
            return locator
    return None


def _read_page(session, rows, params, records) -> None:
    for row in rows:
        if _older_than(row, params["since"]):
            # The cutoff ends the walk rather than skipping one order: history
            # is ordered newest first, so everything after this is older too.
            raise Stop(CUTOFF, f"reached orders older than {params['since']}")
        if row["order_id"] in params["known_ids"] and not params["full"]:
            continue
        records.append(_read_order(session, row))


def _read_order(session, row: dict[str, Any]) -> dict[str, Any]:
    order_id = _safe_order_id(row["order_id"])
    protocol.progress(f"reading order {row.get('display_id') or order_id}")
    response = session.goto(f"{ORDERS_URL}/{order_id}", wait_until="domcontentloaded")
    _guard(session, response)

    payload = session.page.evaluate(_NEXT_DATA_SCRIPT)
    if not payload:
        raise Stop("order_unreadable", f"order {row['order_id']} did not ship its detail")

    record = orders_parse.parse_order(payload)
    # The index knows things the detail page does not repeat, and the detail
    # page resolves what the index could only call "online". Detail wins where
    # they overlap, so the archived record carries one answer, not two.
    for key in ("display_id", "date", "title"):
        record[key] = record.get(key) or row.get(key)
    return record


def _safe_order_id(order_id: Any) -> str:
    """An order id we are willing to put in a URL.

    Validated rather than escaped: an id that is not the shape Walmart uses is
    not an id we mis-encoded, it is a page we do not understand, and quietly
    rewriting it into something navigable would be inventing a destination.
    Stopping keeps the coverage record honest, which skipping would not.
    """
    text = str(order_id or "")
    if not ORDER_ID.match(text):
        raise Stop("order_unreadable", "an order id was not a shape this tool will navigate to")
    return text


def _guard(session, response) -> None:
    """Refuse to keep walking through a throttle or a block."""
    status = getattr(response, "status", None) if response is not None else None
    if status == 429:
        raise Stop("rate_limited", "Walmart is rate-limiting this session; resume in a few minutes")
    if challenge.detect_url(getattr(session.page, "url", None)):
        raise Stop(challenge.CHALLENGE_CODE, challenge.ADVICE)


def _older_than(row: dict[str, Any], since: str | None) -> bool:
    if not since:
        return False
    date = row.get("date")
    if not date:
        # A row we cannot date is not evidence that we have gone far enough
        # back. Stopping on it would silently truncate the archive.
        return False
    return str(date) < str(since)


def _collector(captured: list[Any]):
    def handle(response) -> None:
        if HISTORY_MARKER not in (getattr(response, "url", "") or "").lower():
            return
        try:
            captured.append(response.json())
        except Exception:  # noqa: BLE001 - one unreadable response is not fatal
            return

    return handle


def _pump_until(session, captured: list[Any], wait_seconds: int) -> Any:
    for _ in range(max(1, int(wait_seconds * 1000 / SLICE_MS))):
        if captured:
            return captured[-1]
        session.page.wait_for_timeout(SLICE_MS)
    return captured[-1] if captured else None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_NEXT_DATA_SCRIPT = """
() => {
  const node = document.getElementById('__NEXT_DATA__');
  if (!node) { return null; }
  try { return JSON.parse(node.textContent); } catch (error) { return null; }
}
"""
