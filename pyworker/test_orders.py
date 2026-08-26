"""Walking purchase history against a page double. No browser, no network."""

from __future__ import annotations

import json

import pytest

import browser
import challenge
import orders
from test_orders_parse import IN_STORE_ROW, ONLINE_ROW, history, order


class FakeLocator:
    def __init__(self, present=True, enabled=True):
        self.present = present
        self.enabled = enabled
        self.clicks = 0

    def count(self):
        return 1 if self.present else 0

    def is_enabled(self):
        return self.enabled

    def click(self, **_kwargs):
        self.clicks += 1


class FakeResponse:
    def __init__(self, url="", status=200, payload=None):
        self.url = url
        self.status = status
        self._payload = payload

    def json(self):
        return self._payload


HISTORY_URL = "https://www.walmart.com/orchestra/cph/graphql/PurchaseHistoryV3/abc123"


class FakeSession:
    """Serves a scripted sequence of pages and history responses."""

    def __init__(self, pages=None, orders_by_id=None, next_pages=None, status=200, url=None):
        # pages[0] is the server-rendered first page.
        self.pages = list(pages or [])
        self.orders_by_id = dict(orders_by_id or {})
        self.next_pages = list(next_pages or [])
        self.status = status
        self.url = url or "https://www.walmart.com/orders"
        self.page = self
        self.handlers = []
        self.navigated = []
        self.waits = []
        self.next_button = FakeLocator()
        self.clicked_labels = []
        self._payload = self.pages[0] if self.pages else None

    # -- session surface
    def on_response(self, handler):
        self.handlers.append(handler)

    def goto(self, url, **_kwargs):
        self.navigated.append(url)
        if "/orders/" in url:
            order_id = url.rsplit("/", 1)[-1]
            self._payload = self.orders_by_id.get(order_id)
        else:
            self._payload = self.pages[0] if self.pages else None
        return FakeResponse(url=url, status=self.status)

    def click(self, locator, label=None, **_kwargs):
        self.clicked_labels.append(label)
        locator.click()
        self._deliver_next_page()

    # -- page surface
    def get_by_role(self, _role, name=None):
        return self.next_button if name in orders.NEXT_PAGE_LABELS else FakeLocator(present=False)

    def evaluate(self, _script, _argument=None):
        return self._payload

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)

    def _deliver_next_page(self):
        if not self.next_pages:
            return
        payload = self.next_pages.pop(0)
        for handler in self.handlers:
            handler(FakeResponse(url=HISTORY_URL, payload=payload))

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def graphql_history(orders_rows, cursor=None):
    """The paged response, which arrives without the Next.js envelope."""
    return {
        "data": {
            "purchaseHistory": {
                "orders": orders_rows,
                "pageInfo": {"nextPageCursor": cursor},
            }
        }
    }


def install(monkeypatch, session):
    monkeypatch.setattr(browser, "open_session", lambda _options: session)


REQUEST = {
    "action": "order.sync",
    "options": {"profile_dir": "/tmp/profile", "profile_lock": "/tmp/profile.lock"},
}


def sync(monkeypatch, session, **params):
    install(monkeypatch, session)
    return orders.action_order_sync({**REQUEST, "params": params})


def one_page_session(**kwargs):
    return FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor=None)],
        orders_by_id={"200012345678901": order()},
        **kwargs,
    )


# --- The happy path ------------------------------------------------------------


def test_the_first_page_is_read_from_the_server_rendered_html(monkeypatch):
    session = one_page_session()

    result = sync(monkeypatch, session)

    assert session.navigated[0] == "https://www.walmart.com/orders"
    assert [record["order_id"] for record in result["orders"]] == ["200012345678901"]


def test_each_order_is_read_from_its_own_page(monkeypatch):
    # The list rows carry no usItemId and no price, so an archive built from
    # them would know what was bought but not what it cost.
    session = one_page_session()

    result = sync(monkeypatch, session)

    assert "https://www.walmart.com/orders/200012345678901" in session.navigated
    assert result["orders"][0]["items"][0]["item_id"] == "10450114"


def test_the_detail_resolves_what_the_index_could_only_call_online(monkeypatch):
    session = one_page_session()

    record = sync(monkeypatch, session)["orders"][0]

    assert record["type"] == "delivery"


def test_a_finished_walk_reports_complete_and_keeps_no_cursor(monkeypatch):
    # No cursor means Walmart says there is nothing older. A finished walk
    # no cursor is reported at all: nothing downstream replays one.
    session = one_page_session()

    stopped = sync(monkeypatch, session)["stopped"]

    assert stopped["reason"] == orders.COMPLETE
    assert "cursor" not in stopped


# --- Paging ---------------------------------------------------------------------


def test_paging_clicks_next_rather_than_replaying_a_cursor(monkeypatch):
    # A replayed history request with a partial header set is the shape that
    # earned a 429, and a throttled walk fails halfway through two years.
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor="CURSOR-1")],
        next_pages=[graphql_history([IN_STORE_ROW], cursor=None)],
        orders_by_id={"200012345678901": order(), "300098765432109": order()},
    )

    result = sync(monkeypatch, session)

    assert session.clicked_labels == ["Next page"]
    assert len(result["seen"]) == 2


def test_pages_accumulate_in_the_order_they_were_read(monkeypatch):
    # Three rows over two pages, so the sequence cannot be satisfied by an
    # accumulator that reverses -- two elements cannot tell those apart.
    middle = {**ONLINE_ROW, "id": "250000000000002", "orderDate": "2026-06-01T00:00:00.000Z"}
    oldest = {**IN_STORE_ROW, "id": "210000000000003", "orderDate": "2026-01-05T00:00:00.000Z"}
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW, middle], cursor="CURSOR-1")],
        next_pages=[graphql_history([oldest], cursor=None)],
        orders_by_id={row: order() for row in ("200012345678901", "250000000000002", "210000000000003")},
    )

    seen = [row["order_id"] for row in sync(monkeypatch, session)["seen"]]

    assert seen == ["200012345678901", "250000000000002", "210000000000003"]


def test_the_oldest_date_is_the_oldest_read_not_the_last_row(monkeypatch):
    # The archive prints "nothing older than X" from this. Taking the last row
    # assumes every page came back newest-first; one out-of-order page would
    # turn a coverage claim into a confident lie, so it is a real minimum.
    out_of_order = {**IN_STORE_ROW, "id": "210000000000003", "orderDate": "2024-01-05T00:00:00.000Z"}
    session = FakeSession(
        pages=[history(orders=[out_of_order, ONLINE_ROW], cursor=None)],
        orders_by_id={"210000000000003": order(), "200012345678901": order()},
    )

    stopped = sync(monkeypatch, session)["stopped"]

    assert stopped["oldest_date"] == "2024-01-05T00:00:00.000Z"


def test_undated_rows_do_not_erase_the_oldest_date(monkeypatch):
    # A row we cannot date is not a date of None; reporting no coverage floor
    # at all would understate what we actually have.
    undated = {**IN_STORE_ROW, "id": "210000000000003", "orderDate": None}
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW, undated], cursor=None)],
        orders_by_id={"200012345678901": order(), "210000000000003": order()},
    )

    stopped = sync(monkeypatch, session)["stopped"]

    assert stopped["oldest_date"] == "2026-08-20T14:02:00.000Z"


def test_a_missing_next_control_is_not_the_end_of_history(monkeypatch):
    # The cursor promised another page. Not finding the button means the page
    # changed, and calling that "complete" would archive a partial history as
    # if it were whole.
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor="CURSOR-1")],
        orders_by_id={"200012345678901": order()},
    )
    session.next_button = FakeLocator(present=False)

    stopped = sync(monkeypatch, session)["stopped"]

    assert stopped["reason"] == orders.NO_NEXT_PAGE


def test_a_page_ceiling_stops_an_unbounded_walk(monkeypatch):
    # An unbounded loop against someone else's site is how a bug becomes abuse.
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor="CURSOR-1")],
        next_pages=[graphql_history([ONLINE_ROW], cursor="CURSOR-2")],
        orders_by_id={"200012345678901": order()},
    )

    stopped = sync(monkeypatch, session, max_pages=1)["stopped"]

    assert stopped["reason"] == orders.PAGE_LIMIT


# --- Stopping -----------------------------------------------------------------------


def test_a_page_entirely_older_than_the_window_ends_the_walk(monkeypatch):
    # The frontier: every dated row on the page is older than the window.
    session = FakeSession(
        pages=[history(orders=[IN_STORE_ROW], cursor="CURSOR-1")],
        orders_by_id={},
    )

    result = sync(monkeypatch, session, since="2026-08-19")

    assert result["stopped"]["reason"] == orders.CUTOFF
    assert result["orders"] == []


# The old shape of this test asserted the opposite and held the defect in place:
# the cutoff was raised on the first out-of-window row, abandoning every row
# after it on that page. That bet completeness on Walmart returning history in
# date order -- the assumption _oldest_date refuses to make, in the same file,
# for the same reason. A page shaped [old, in-window] dropped the second order
# silently, reported cutoff, exited 0, and repeated the miss on every re-run.
def test_an_out_of_order_page_does_not_lose_the_order_after_the_old_one(monkeypatch):
    session = FakeSession(
        pages=[history(orders=[IN_STORE_ROW, ONLINE_ROW], cursor=None)],
        orders_by_id={"200012345678901": order()},
    )

    result = sync(monkeypatch, session, since="2026-08-19")

    assert [record["order_id"] for record in result["orders"]] == ["200012345678901"]
    assert result["stopped"]["reason"] == orders.COMPLETE


def test_a_mixed_page_is_not_a_frontier_and_the_walk_continues(monkeypatch):
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW, IN_STORE_ROW], cursor="CURSOR-1")],
        next_pages=[graphql_history([IN_STORE_ROW], cursor="CURSOR-2")],
        orders_by_id={"200012345678901": order()},
    )

    result = sync(monkeypatch, session, since="2026-08-19")

    # The in-window order is read, and the walk goes on to the next page, which
    # is entirely old and therefore is the frontier.
    assert [record["order_id"] for record in result["orders"]] == ["200012345678901"]
    assert result["stopped"]["reason"] == orders.CUTOFF


def test_an_undated_row_does_not_end_the_walk(monkeypatch):
    # A row we cannot date is not evidence that we have gone back far enough,
    # and stopping on it would silently truncate the archive.
    undated = {**ONLINE_ROW, "orderDate": None}
    session = FakeSession(
        pages=[history(orders=[undated], cursor=None)],
        orders_by_id={"200012345678901": order()},
    )

    assert sync(monkeypatch, session, since="2026-01-01")["stopped"]["reason"] != orders.CUTOFF


def test_a_block_stops_the_walk_and_keeps_what_it_read(monkeypatch):
    # A delivered partial result beats a wedged exit: the archive stores what
    # arrived and marks itself incomplete.
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor="CURSOR-1")],
        orders_by_id={"200012345678901": order()},
    )
    session.url = "https://identity.walmart.com/blocked"

    result = sync(monkeypatch, session)

    assert result["stopped"]["reason"] == challenge.CHALLENGE_CODE
    assert result["orders"] == []


def test_a_throttle_stops_the_walk_resumably(monkeypatch):
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor="CURSOR-1")],
        orders_by_id={"200012345678901": order()},
        status=429,
    )

    stopped = sync(monkeypatch, session)["stopped"]

    assert stopped["reason"] == "rate_limited"


def test_a_stopped_walk_keeps_its_cursor_for_the_next_run(monkeypatch):
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor="CURSOR-1")],
        next_pages=[],
        orders_by_id={"200012345678901": order()},
    )
    # The next page never arrives: the click happens, nothing comes back.
    result = sync(monkeypatch, session, wait_seconds=1)

    assert result["stopped"]["reason"] == "history_unreadable"


def test_an_unreadable_first_page_is_not_an_empty_history(monkeypatch):
    session = FakeSession(pages=[None])

    result = sync(monkeypatch, session)

    assert result["stopped"]["reason"] == "history_unreadable"
    assert result["orders"] == []


def test_a_hostile_order_id_never_becomes_a_url(monkeypatch):
    # Same value, same provenance as the one archive.rb sanitizes before it
    # becomes a filesystem path. It was trusted here because it becomes a URL
    # instead, and an encoded traversal in it would have navigated somewhere
    # this tool must never go.
    hostile = {**ONLINE_ROW, "id": "200012345678901/..%2F..%2Fcheckout"}
    session = FakeSession(pages=[history(orders=[hostile], cursor=None)])

    result = sync(monkeypatch, session)

    assert result["stopped"]["reason"] == "order_unreadable"
    assert not [url for url in session.navigated if "checkout" in url.lower()]


def test_an_ordinary_order_id_still_navigates(monkeypatch):
    session = one_page_session()

    sync(monkeypatch, session)

    assert "https://www.walmart.com/orders/200012345678901" in session.navigated


# --- Incremental sync --------------------------------------------------------------


def test_known_orders_are_not_refetched(monkeypatch):
    # This is what makes a re-sync cheap: the detail page is the expensive part.
    session = one_page_session()

    result = sync(monkeypatch, session, known_ids=["200012345678901"])

    assert result["orders"] == []
    assert "https://www.walmart.com/orders/200012345678901" not in session.navigated


def test_full_refetches_a_known_order(monkeypatch):
    session = one_page_session()

    result = sync(monkeypatch, session, known_ids=["200012345678901"], full=True)

    assert len(result["orders"]) == 1


def test_a_skipped_order_is_still_reported_as_seen(monkeypatch):
    # The caller needs to know the walk covered it, or an incremental sync
    # looks like a walk that found nothing.
    session = one_page_session()

    result = sync(monkeypatch, session, known_ids=["200012345678901"])

    assert [row["order_id"] for row in result["seen"]] == ["200012345678901"]


# --- Parameters -----------------------------------------------------------------------


def test_a_nonsense_page_count_is_refused_before_the_browser_opens(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("parameters must be validated before a browser opens")

    monkeypatch.setattr(browser, "open_session", explode)

    with pytest.raises(ValueError):
        orders.action_order_sync({**REQUEST, "params": {"max_pages": "lots"}})


def test_known_ids_must_be_a_list(monkeypatch):
    with pytest.raises(ValueError):
        orders.normalize_params({"params": {"known_ids": "200012345678901"}})


# --- Constants that fail open -------------------------------------------------
#
# Audited with the same question as the denylists (docs/TESTING.md): which
# direction does a wrong value fail in? Lowering MAX_PAGES stops the walk early
# with page_limit and a resumable archive -- visible, documented, safe. Raising
# it means an unbounded loop against someone else's site, unattended, which is
# how a bug becomes abuse. Raising PAGE_SIZE asks Walmart for a page shape we
# have never observed, with a 429 as the price of being wrong.
#
# Both are therefore pinned, and both survived mutation before these existed.


def test_the_page_ceiling_is_what_we_agreed_to():
    assert orders.MAX_PAGES == 200, (
        "raising this lets an unattended sync walk further against Walmart than "
        "anyone has agreed to; it is a ceiling, not a tuning knob"
    )


def test_the_page_size_stays_where_walmart_put_it():
    assert orders.PAGE_SIZE == 5, (
        "this is Walmart's own page size, observed, not chosen; raising it "
        "hopefully is an experiment paid for with a 429"
    )


def test_a_request_cannot_ask_to_walk_past_the_ceiling():
    """The ceiling was a default: any max_pages in the request was honoured.

    Our CLI never sends one, so nothing was wrong in practice. The worker is a
    separate process though, and a limit that the caller can raise is not a
    limit.
    """
    walked = orders.normalize_params({"params": {"max_pages": 10_000}})

    assert walked["max_pages"] == orders.MAX_PAGES


def test_a_request_may_still_ask_for_fewer_pages():
    # The other half of the clamp. Without this, "asked for 10,000, got 200"
    # is equally satisfied by ignoring max_pages entirely -- which is exactly
    # what the first draft of these two tests did, both of them green.
    assert orders.normalize_params({"params": {"max_pages": 3}})["max_pages"] == 3


def test_an_order_id_longer_than_the_bound_is_refused():
    """The {1,64} bound had no test; only the character class did.

    An id is Walmart's data on its way into a URL and a filename, and "any
    length of otherwise-legal characters" is not the shape we validated.
    """
    assert orders.ORDER_ID.match("2" * 64)
    assert not orders.ORDER_ID.match("2" * 65)


# The cursor was reported to Ruby, written to the archive, and never read by
# anything. The walk clicks Next rather than replaying it -- deliberately, since
# replaying earned a 429 -- so the field only ever travelled outward. An
# identifier with no consumer is a liability, so it no longer crosses the
# boundary at all.
def test_no_stop_note_carries_a_cursor_out_of_the_worker(monkeypatch):
    cases = [
        (one_page_session(), {}),
        (
            FakeSession(
                pages=[history(orders=[ONLINE_ROW], cursor="CURSOR-1")],
                next_pages=[graphql_history([ONLINE_ROW], cursor="CURSOR-2")],
                orders_by_id={"200012345678901": order()},
            ),
            {"max_pages": 1},
        ),
    ]
    for session, kwargs in cases:
        result = sync(monkeypatch, session, **kwargs)

        assert "cursor" not in result["stopped"], result["stopped"]
        assert "CURSOR" not in json.dumps(result["stopped"])


# known_ids arrives from the caller, and the worker protocol is directly
# invocable, so it is untrusted input. The membership test used to run before
# validation, which meant a malformed order id could be waved through as
# "already covered" by handing the worker that same string. The archive would
# then report complete while quietly holding nothing for it.
def test_a_malformed_id_is_refused_even_when_the_caller_calls_it_known(monkeypatch):
    bad = {**ONLINE_ROW, "id": "../../checkout"}
    session = FakeSession(
        pages=[history(orders=[bad], cursor=None)],
        orders_by_id={},
    )

    result = sync(monkeypatch, session, known_ids=["../../checkout"])

    assert result["stopped"]["reason"] == "order_unreadable"
    assert result["orders"] == []


def test_a_malformed_id_is_refused_when_it_is_out_of_the_window_too(monkeypatch):
    # Validation happens before the window test as well: an id we will not
    # navigate to is not made acceptable by being old.
    bad = {**IN_STORE_ROW, "id": "not a valid id"}
    session = FakeSession(pages=[history(orders=[bad], cursor=None)], orders_by_id={})

    result = sync(monkeypatch, session, since="2026-08-19")

    assert result["stopped"]["reason"] == "order_unreadable"


def test_a_well_formed_known_id_is_still_skipped(monkeypatch):
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor=None)],
        orders_by_id={"200012345678901": order()},
    )

    result = sync(monkeypatch, session, known_ids=["200012345678901"])

    assert result["orders"] == []
    assert result["stopped"]["reason"] == orders.COMPLETE


# A protected id is one the caller cannot store even if we fetch it: a record
# written by a newer CLI, which it refuses to overwrite. It rode in known_ids,
# which --full empties and which this function ignores under full -- so the
# pre-flight promised those records would be left untouched and --full then
# fetched every one of them, only for the write to be refused after the network.
def test_a_protected_id_is_skipped_even_under_full(monkeypatch):
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor=None)],
        orders_by_id={"200012345678901": order()},
    )

    result = sync(monkeypatch, session, full=True, protected_ids=["200012345678901"])

    assert result["orders"] == []
    detail_pages = [url for url in session.navigated if "/orders/" in url]
    assert detail_pages == [], "a protected order must cost no page load"


def test_a_known_id_is_still_fetched_under_full(monkeypatch):
    # The difference between the two lists: full means "re-read what we have".
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor=None)],
        orders_by_id={"200012345678901": order()},
    )

    result = sync(monkeypatch, session, full=True, known_ids=["200012345678901"])

    assert [record["order_id"] for record in result["orders"]] == ["200012345678901"]


def test_a_protected_id_is_skipped_on_an_incremental_sync_too(monkeypatch):
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor=None)],
        orders_by_id={"200012345678901": order()},
    )

    result = sync(monkeypatch, session, protected_ids=["200012345678901"])

    assert result["orders"] == []


def test_protected_ids_must_be_a_list():
    with pytest.raises(ValueError, match="protected_ids"):
        orders.normalize_params({"params": {"protected_ids": "200012345678901"}})


# --- Bounded windows ----------------------------------------------------------------


def dated_row(order_id, date):
    return {**ONLINE_ROW, "id": order_id, "orderDate": f"{date}T12:00:00.000Z"}


def year_window(year):
    return {"since": f"{year}-01-01", "before": f"{year + 1}-01-01"}


# `--year 2024` said "sync one calendar year" in the help, in the design and in
# the class comment, and sent only a lower bound -- so in 2026 it archived 2024,
# 2025 and 2026. Rows are deliberately out of date order here: nothing may
# depend on Walmart returning them sorted.
def test_only_the_requested_year_is_fetched(monkeypatch):
    rows = [
        dated_row("200000000000001", "2023-06-01"),
        dated_row("200000000000002", "2025-06-01"),
        dated_row("200000000000003", "2024-06-01"),
        dated_row("200000000000004", "2026-06-01"),
    ]
    session = FakeSession(
        pages=[history(orders=rows, cursor=None)],
        orders_by_id={row["id"]: order(id=row["id"]) for row in rows},
    )

    result = sync(monkeypatch, session, **year_window(2024))

    assert [record["order_id"] for record in result["orders"]] == ["200000000000003"]


def test_a_row_above_the_window_costs_no_page_load(monkeypatch):
    rows = [dated_row("200000000000002", "2025-06-01")]
    session = FakeSession(
        pages=[history(orders=rows, cursor=None)],
        orders_by_id={"200000000000002": order(id="200000000000002")},
    )

    sync(monkeypatch, session, **year_window(2024))

    assert [url for url in session.navigated if "/orders/" in url] == []


# The upper bound must never stop the walk: rows above the window are exactly
# what we page through on the way down to the requested year.
def test_a_page_entirely_above_the_window_keeps_walking(monkeypatch):
    session = FakeSession(
        pages=[history(orders=[dated_row("200000000000002", "2025-06-01")], cursor="C1")],
        next_pages=[graphql_history([dated_row("200000000000003", "2024-06-01")], cursor=None)],
        orders_by_id={"200000000000003": order(id="200000000000003")},
    )

    result = sync(monkeypatch, session, **year_window(2024))

    assert [record["order_id"] for record in result["orders"]] == ["200000000000003"]
    assert result["stopped"]["reason"] == orders.COMPLETE


# Only the lower edge is a frontier.
def test_the_walk_still_stops_below_the_window(monkeypatch):
    session = FakeSession(
        pages=[history(orders=[dated_row("200000000000001", "2023-06-01")], cursor="C1")],
        orders_by_id={},
    )

    result = sync(monkeypatch, session, **year_window(2024))

    assert result["stopped"]["reason"] == orders.CUTOFF


def test_an_open_topped_window_is_unaffected(monkeypatch):
    rows = [dated_row("200000000000004", "2026-06-01"), dated_row("200000000000003", "2024-06-01")]
    session = FakeSession(
        pages=[history(orders=rows, cursor=None)],
        orders_by_id={row["id"]: order(id=row["id"]) for row in rows},
    )

    result = sync(monkeypatch, session, since="2024-01-01")

    assert len(result["orders"]) == 2, "no upper bound means up to now"


# The boundary decides which year an order belongs to, and both ends of it are
# a single character in the comparison. A mutant flipping >= to > survived until
# these existed.
def test_the_first_instant_of_the_next_year_is_outside_the_window(monkeypatch):
    rows = [dated_row("200000000000005", "2025-01-01")]
    session = FakeSession(
        pages=[history(orders=rows, cursor=None)],
        orders_by_id={"200000000000005": order(id="200000000000005")},
    )

    result = sync(monkeypatch, session, **year_window(2024))

    assert result["orders"] == [], "before is exclusive"


def test_the_first_day_of_the_requested_year_is_inside_the_window(monkeypatch):
    rows = [dated_row("200000000000006", "2024-01-01")]
    session = FakeSession(
        pages=[history(orders=rows, cursor=None)],
        orders_by_id={"200000000000006": order(id="200000000000006")},
    )

    result = sync(monkeypatch, session, **year_window(2024))

    assert [r["order_id"] for r in result["orders"]] == ["200000000000006"], "since is inclusive"


def test_the_last_day_of_the_requested_year_is_inside_the_window(monkeypatch):
    rows = [dated_row("200000000000007", "2024-12-31")]
    session = FakeSession(
        pages=[history(orders=rows, cursor=None)],
        orders_by_id={"200000000000007": order(id="200000000000007")},
    )

    result = sync(monkeypatch, session, **year_window(2024))

    assert [r["order_id"] for r in result["orders"]] == ["200000000000007"]


# The bound is a date and the row's date is whatever Walmart sent, compared as
# strings. Every timestamped row sorts strictly after a bare date with the same
# day, so >= and > only diverge when the row itself carries no time -- which the
# parser passes through unmodified. That row belongs to the next year.
def test_a_date_only_row_on_the_boundary_is_outside_the_window(monkeypatch):
    rows = [{**ONLINE_ROW, "id": "200000000000008", "orderDate": "2025-01-01"}]
    session = FakeSession(
        pages=[history(orders=rows, cursor=None)],
        orders_by_id={"200000000000008": order(id="200000000000008")},
    )

    result = sync(monkeypatch, session, **year_window(2024))

    assert result["orders"] == []


def test_a_date_only_row_on_the_lower_edge_is_inside_the_window(monkeypatch):
    rows = [{**ONLINE_ROW, "id": "200000000000009", "orderDate": "2024-01-01"}]
    session = FakeSession(
        pages=[history(orders=rows, cursor=None)],
        orders_by_id={"200000000000009": order(id="200000000000009")},
    )

    result = sync(monkeypatch, session, **year_window(2024))

    assert [r["order_id"] for r in result["orders"]] == ["200000000000009"]
