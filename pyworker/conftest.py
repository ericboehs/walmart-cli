"""Browser/page doubles.

Tests use the real browser.Session so the navigation and click guards are
exercised, and fake only the Playwright objects underneath it.
"""

from __future__ import annotations

import pytest

import browser


class FakeLocator:
    def __init__(self, page, selector, exists=True, text="", attributes=None, readable=True):
        self.page = page
        self.selector = selector
        self.exists = exists
        self.text = text
        self.attributes = attributes or {}
        # readable=False models a detached or mid-navigation element: it is
        # there, but nothing about it can be read.
        self.readable = readable

    @property
    def first(self):
        return self

    def is_visible(self, timeout=None):  # noqa: ARG002 - mirrors the Playwright signature
        if not self.exists:
            raise TimeoutError(f"no element for {self.selector}")
        return True

    def wait_for(self, state=None, timeout=None):  # noqa: ARG002
        if not self.exists:
            raise TimeoutError(f"no element for {self.selector}")

    def inner_text(self, timeout=None):  # noqa: ARG002
        if not self.readable:
            raise TimeoutError(f"element detached: {self.selector}")
        return self.text

    def get_attribute(self, name, timeout=None):  # noqa: ARG002
        if not self.readable:
            raise TimeoutError(f"element detached: {self.selector}")
        return self.attributes.get(name)

    def fill(self, value):
        if not self.exists:
            raise TimeoutError(f"no element for {self.selector}")
        self.page.filled.append((self.selector, value))

    def click(self, **kwargs):  # noqa: ARG002
        if not self.exists:
            raise TimeoutError(f"no element for {self.selector}")
        self.page.clicked.append(self.selector)


class FakePage:
    """A page whose visible selectors and URL the test controls."""

    def __init__(
        self,
        url="https://www.walmart.com/account/login",
        visible=(),
        labels=None,
        attributes=None,
        unreadable=(),
        closed=False,
    ):
        self.url = url
        self.visible = set(visible)
        self.labels = labels or {}
        self.attributes = attributes or {}
        self.unreadable = set(unreadable)
        # A closed page models a crashed or user-closed window: every read
        # fails, which must not be mistaken for a selector that drifted.
        self.closed = closed
        self.navigations = []
        self.filled = []
        self.clicked = []
        # Every pump slice, in milliseconds. Recon asserts on these: in the sync
        # API this call is the only thing that lets response handlers run.
        self.waits = []

    def wait_for_timeout(self, milliseconds):
        if self.closed:
            raise RuntimeError("Target page, context or browser has been closed")
        self.waits.append(milliseconds)

    def is_closed(self):
        return self.closed

    def locator(self, selector):
        if self.closed:
            raise RuntimeError("Target page, context or browser has been closed")
        return FakeLocator(
            self,
            selector,
            exists=selector in self.visible,
            text=self.labels.get(selector, ""),
            attributes=self.attributes.get(selector, {}),
            readable=selector not in self.unreadable,
        )

    def goto(self, url, **kwargs):  # noqa: ARG002
        if self.closed:
            raise RuntimeError("Target page, context or browser has been closed")
        self.navigations.append(url)
        self.url = url
        return None


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.closed = False
        self.handlers = {}

    def new_page(self):
        return self.pages[0]

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_page():
    return FakePage()


@pytest.fixture
def make_session():
    def _make(page):
        options = browser.BrowserOptions(profile_dir="/tmp/profile", profile_lock="/tmp/profile.lock")
        return browser.Session(FakeContext(page), options)

    return _make
