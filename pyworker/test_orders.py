"""Walking purchase history against a page double. No browser, no network."""

from __future__ import annotations

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
    # holding a session-scoped cursor is an identifier kept for no reason.
    session = one_page_session()

    stopped = sync(monkeypatch, session)["stopped"]

    assert stopped["reason"] == orders.COMPLETE
    assert stopped["cursor"] is None


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
    assert stopped["cursor"] == "CURSOR-1"


def test_a_page_ceiling_stops_an_unbounded_walk(monkeypatch):
    # An unbounded loop against someone else's site is how a bug becomes abuse.
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW], cursor="CURSOR-1")],
        next_pages=[graphql_history([ONLINE_ROW], cursor="CURSOR-2")],
        orders_by_id={"200012345678901": order()},
    )

    stopped = sync(monkeypatch, session, max_pages=1)["stopped"]

    assert stopped["reason"] == orders.PAGE_LIMIT
    assert stopped["cursor"] == "CURSOR-1"


# --- Stopping -----------------------------------------------------------------------


def test_the_cutoff_ends_the_walk_rather_than_skipping_one_order(monkeypatch):
    # History is newest first, so everything past the cutoff is older too.
    session = FakeSession(
        pages=[history(orders=[ONLINE_ROW, IN_STORE_ROW], cursor="CURSOR-1")],
        orders_by_id={"200012345678901": order()},
    )

    result = sync(monkeypatch, session, since="2026-08-19")

    assert result["stopped"]["reason"] == orders.CUTOFF
    assert [record["order_id"] for record in result["orders"]] == ["200012345678901"]


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
    assert result["stopped"]["cursor"] == "CURSOR-1"


def test_an_unreadable_first_page_is_not_an_empty_history(monkeypatch):
    session = FakeSession(pages=[None])

    result = sync(monkeypatch, session)

    assert result["stopped"]["reason"] == "history_unreadable"
    assert result["orders"] == []


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
