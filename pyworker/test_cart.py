"""Reading the live cart. No browser, no network -- the session is a fake."""

from __future__ import annotations

import pytest

import browser
import cart
import challenge
from test_cart_parse import MILK
from test_cart_parse import cart as cart_payload

GETCART_URL = (
    "https://www.walmart.com/orchestra/home/graphql/getCart/fbd4f2dd69076197b55ff3a6cd7d58a9832b9e/getCart"
)


class FakeResponse:
    def __init__(self, url="", status=200, payload=None, unreadable=False):
        self.url = url
        self.status = status
        self._payload = payload
        self._unreadable = unreadable

    def json(self):
        if self._unreadable:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """A page that emits responses after a set number of pump slices."""

    def __init__(self, responses=(), status=200, url="https://www.walmart.com/cart", after=1):
        self.responses = list(responses)
        self.status = status
        self.url = url
        self.after = after
        self.page = self
        self.handlers = []
        self.navigated = []
        self.waits = []

    # -- session surface
    def on_response(self, handler):
        self.handlers.append(handler)

    def goto(self, url, **_kwargs):
        self.navigated.append(url)
        return FakeResponse(url=url, status=self.status)

    # -- page surface
    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        if len(self.waits) >= self.after:
            for response in self.responses:
                for handler in self.handlers:
                    handler(response)
            self.responses = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def getcart(payload, url=GETCART_URL, **kwargs):
    return FakeResponse(url=url, payload=payload, **kwargs)


def install(monkeypatch, session):
    monkeypatch.setattr(browser, "open_session", lambda _options: session)


REQUEST = {
    "action": "cart.show",
    "options": {"profile_dir": "/tmp/profile", "profile_lock": "/tmp/profile.lock"},
}


def test_the_cart_is_read_from_the_page_own_request(monkeypatch):
    # We never replay getCart: a captured cart URL replayed with partial headers
    # earns a 429, and letting the page ask means we never hold the cart id.
    session = FakeSession(responses=[getcart(cart_payload())])
    install(monkeypatch, session)

    data = cart.action_cart_show(REQUEST)

    assert session.navigated == ["https://www.walmart.com/cart"]
    assert data["line_count"] == 2


def test_the_cart_id_never_reaches_the_result(monkeypatch):
    session = FakeSession(responses=[getcart(cart_payload())])
    install(monkeypatch, session)

    assert "SESSION-CART-UUID" not in repr(cart.action_cart_show(REQUEST))


def test_unrelated_traffic_is_ignored(monkeypatch):
    noise = FakeResponse(url="https://www.walmart.com/orchestra/home/graphql/Location/abc", payload={"x": 1})
    session = FakeSession(responses=[noise, getcart(cart_payload())])
    install(monkeypatch, session)

    assert cart.action_cart_show(REQUEST)["line_count"] == 2


def test_the_newest_cart_read_wins(monkeypatch):
    # The page may fetch the cart more than once; the last one reflects the
    # cart now, and an earlier one can be a pre-merge snapshot.
    stale = getcart(cart_payload(lines=[MILK]))
    fresh = getcart(cart_payload())
    session = FakeSession(responses=[stale, fresh])
    install(monkeypatch, session)

    assert cart.action_cart_show(REQUEST)["line_count"] == 2


def test_an_unreadable_response_does_not_abort_the_read(monkeypatch):
    session = FakeSession(responses=[getcart(None, unreadable=True), getcart(cart_payload())])
    install(monkeypatch, session)

    assert cart.action_cart_show(REQUEST)["line_count"] == 2


def test_a_cart_that_never_arrives_is_not_an_empty_cart(monkeypatch):
    # This is the failure that would send someone to the store for groceries
    # they already ordered.
    session = FakeSession(responses=[])
    install(monkeypatch, session)

    with pytest.raises(cart.CartUnreadable) as raised:
        cart.action_cart_show({**REQUEST, "params": {"wait_seconds": 1}})

    assert raised.value.code == "cart_unreadable"


def test_an_empty_cart_is_reported_as_empty(monkeypatch):
    session = FakeSession(responses=[getcart(cart_payload(lines=[]))])
    install(monkeypatch, session)

    data = cart.action_cart_show(REQUEST)

    assert data["lines"] == []
    assert data["line_count"] == 0


def test_landing_on_a_block_page_stops_the_read(monkeypatch):
    session = FakeSession(url="https://identity.walmart.com/blocked")
    install(monkeypatch, session)

    with pytest.raises(challenge.Blocked):
        cart.action_cart_show(REQUEST)


def test_a_block_delivered_as_the_cart_response_is_not_an_empty_cart(monkeypatch):
    block = {"blockScript": "/px/c.js", "jsClientSrc": "/px/x.js", "appId": "PXtest"}
    session = FakeSession(responses=[getcart(block)])
    install(monkeypatch, session)

    with pytest.raises(challenge.Blocked):
        cart.action_cart_show(REQUEST)


def test_a_throttled_cart_page_says_so(monkeypatch):
    session = FakeSession(responses=[getcart(cart_payload())], status=429)
    install(monkeypatch, session)

    with pytest.raises(RuntimeError) as raised:
        cart.action_cart_show(REQUEST)

    assert "rate-limiting" in str(raised.value)


def test_the_wait_is_pumped_rather_than_slept(monkeypatch):
    # Playwright's sync API runs response handlers only while something is
    # pumping. A sleep here would wait the full timeout and then report nothing.
    session = FakeSession(responses=[getcart(cart_payload())], after=3)
    install(monkeypatch, session)

    cart.action_cart_show(REQUEST)

    assert session.waits == [cart.SLICE_MS] * 3


def test_a_nonsense_wait_is_refused_before_the_browser_opens(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("parameters must be validated before a browser opens")

    monkeypatch.setattr(browser, "open_session", explode)

    with pytest.raises(ValueError):
        cart.action_cart_show({**REQUEST, "params": {"wait_seconds": "soon"}})
