"""Session lifecycle and guard tests. No real browser is launched."""

from __future__ import annotations

import multiprocessing
import os
import pathlib
import sys
import time
import types

import pytest

import browser
import safety
from conftest import FakePage


def test_goto_refuses_a_forbidden_destination(make_session):
    session = make_session(FakePage())

    with pytest.raises(safety.UnsafeNavigation):
        session.goto("https://www.walmart.com/checkout")

    assert session.page.navigations == []


def test_goto_allows_ordinary_pages(make_session):
    session = make_session(FakePage())
    session.goto("https://www.walmart.com/cart")

    assert session.page.navigations == ["https://www.walmart.com/cart"]


def test_click_refuses_a_forbidden_control(make_session):
    page = FakePage(visible={"button.pay"}, labels={"button.pay": "Place order"})
    session = make_session(page)

    with pytest.raises(safety.UnsafeNavigation):
        session.click(page.locator("button.pay"))

    assert page.clicked == []


# The blocker this file exists to prevent: an unreadable label must not become
# an unchecked click. The unknown button might be the one that submits an order.
def test_click_fails_closed_when_the_label_cannot_be_read(make_session):
    page = FakePage(visible={"button.mystery"}, unreadable={"button.mystery"})
    session = make_session(page)

    with pytest.raises(safety.UnsafeNavigation) as excinfo:
        session.click(page.locator("button.mystery"))

    assert "could not be read" in str(excinfo.value)
    assert page.clicked == []


def test_click_fails_closed_on_an_empty_label(make_session):
    page = FakePage(visible={"button.blank"}, labels={"button.blank": "   "})
    session = make_session(page)

    with pytest.raises(safety.UnsafeNavigation):
        session.click(page.locator("button.blank"))

    assert page.clicked == []


def test_click_accepts_an_explicit_safe_label_when_the_dom_is_unreadable(make_session):
    page = FakePage(visible={"button.add"}, unreadable={"button.add"})
    session = make_session(page)

    session.click(page.locator("button.add"), label="Add to cart")

    assert page.clicked == ["button.add"]


def test_an_explicit_label_is_still_checked(make_session):
    page = FakePage(visible={"button.x"}, unreadable={"button.x"})
    session = make_session(page)

    with pytest.raises(safety.UnsafeNavigation):
        session.click(page.locator("button.x"), label="Continue to checkout")

    assert page.clicked == []


def test_click_falls_back_to_aria_label(make_session):
    page = FakePage(
        visible={"button.icon"},
        labels={"button.icon": ""},
        attributes={"button.icon": {"aria-label": "Increase quantity"}},
    )
    session = make_session(page)
    session.click(page.locator("button.icon"))

    assert page.clicked == ["button.icon"]


def test_an_aria_label_naming_a_forbidden_control_is_refused(make_session):
    page = FakePage(
        visible={"button.icon"},
        labels={"button.icon": ""},
        attributes={"button.icon": {"aria-label": "Place order"}},
    )
    session = make_session(page)

    with pytest.raises(safety.UnsafeNavigation):
        session.click(page.locator("button.icon"))


def test_the_raw_playwright_context_is_not_public(make_session):
    session = make_session(FakePage())

    assert not hasattr(session, "context")


def test_on_response_subscribes_through_the_session(make_session):
    session = make_session(FakePage())
    calls = []
    session.on_response(calls.append)

    assert session._context.handlers["response"] == [calls.append]


def test_click_allows_an_add_to_cart_control(make_session):
    page = FakePage(visible={"button.add"}, labels={"button.add": "Add to cart"})
    session = make_session(page)
    session.click(page.locator("button.add"))

    assert page.clicked == ["button.add"]


def test_options_are_immutable():
    options = browser.BrowserOptions(profile_dir="/p", profile_lock="/p.lock")

    with pytest.raises(Exception):  # noqa: B017 - dataclasses.FrozenInstanceError
        options.headless = False


def test_visible_options_are_constructed_explicitly():
    options = browser.BrowserOptions.from_request({"profile_dir": "/p", "headless": True}, headless=False)

    assert options.headless is False


class FakePlaywright:
    """Just enough of the Playwright namespace to test the launch fallback."""

    def __init__(self, fail_with_channel=False, fail_always=False):
        self.fail_with_channel = fail_with_channel
        self.fail_always = fail_always
        self.calls = []
        self.chromium = self

    def launch_persistent_context(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_always:
            raise RuntimeError("no browser at all")
        if self.fail_with_channel and "channel" in kwargs:
            raise RuntimeError("channel chrome is not installed")
        return object()


def test_launch_falls_back_off_a_bad_channel_and_says_so(capsys):
    pw = FakePlaywright(fail_with_channel=True)
    options = browser.BrowserOptions(profile_dir="/p", profile_lock="/p.lock", browser_channel="chrome")

    browser.launch_context(pw, options)

    assert len(pw.calls) == 2
    assert "channel" in pw.calls[0]
    assert "channel" not in pw.calls[1]
    assert "falling back" in capsys.readouterr().err


def test_launch_uses_the_requested_channel_when_it_works():
    pw = FakePlaywright()
    options = browser.BrowserOptions(profile_dir="/p", profile_lock="/p.lock", browser_channel="chrome")

    browser.launch_context(pw, options)

    assert len(pw.calls) == 1
    assert pw.calls[0]["channel"] == "chrome"


def test_launch_failure_without_a_channel_is_reported_as_unavailable(monkeypatch):
    monkeypatch.setattr(browser, "_default_channel", lambda: None)
    pw = FakePlaywright(fail_always=True)
    options = browser.BrowserOptions(profile_dir="/p", profile_lock="/p.lock")

    with pytest.raises(browser.BrowserUnavailable):
        browser.launch_context(pw, options)


def test_launch_failure_after_the_fallback_is_reported_as_unavailable():
    pw = FakePlaywright(fail_always=True)
    options = browser.BrowserOptions(profile_dir="/p", profile_lock="/p.lock", browser_channel="chrome")

    with pytest.raises(browser.BrowserUnavailable):
        browser.launch_context(pw, options)


def test_headless_choice_reaches_the_launch_call(monkeypatch):
    monkeypatch.setattr(browser, "_default_channel", lambda: None)
    pw = FakePlaywright()
    options = browser.BrowserOptions(profile_dir="/p", profile_lock="/p.lock", headless=False)

    browser.launch_context(pw, options)

    assert pw.calls[0]["headless"] is False


def test_options_come_from_the_request():
    options = browser.BrowserOptions.from_request(
        {
            "profile_dir": "/data/walmart/browser-profile",
            "profile_lock": "/data/walmart/browser-profile.lock",
            "headless": False,
            "browser_channel": "chrome",
        }
    )

    assert options.profile_dir == "/data/walmart/browser-profile"
    assert options.headless is False
    assert options.browser_channel == "chrome"


def test_options_default_the_lock_next_to_the_profile():
    options = browser.BrowserOptions.from_request({"profile_dir": "/data/profile"})

    assert options.profile_lock == "/data/profile.lock"
    assert options.headless is True


def test_profile_lock_is_exclusive(tmp_path):
    lock = str(tmp_path / "profile.lock")

    with browser.profile_lock(lock):
        assert os.path.exists(lock)
        proc = multiprocessing.get_context("spawn").Process(target=_take_lock, args=(lock,))
        proc.start()
        proc.join(30)
        # Exit code 3 is our agreed "already locked" signal from the child.
        assert proc.exitcode == 3


def test_profile_lock_is_released_afterwards(tmp_path):
    lock = str(tmp_path / "profile.lock")

    with browser.profile_lock(lock):
        pass
    with browser.profile_lock(lock):
        pass  # a second acquisition must succeed


def test_lock_file_is_owner_only(tmp_path):
    lock = str(tmp_path / "profile.lock")

    with browser.profile_lock(lock):
        assert oct(os.stat(lock).st_mode & 0o777) == "0o600"


def _take_lock(lock_path: str) -> None:
    """Child process: exit 3 if the lock is already held."""
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import browser as child_browser

    try:
        with child_browser.profile_lock(lock_path):
            time.sleep(0.1)
    except child_browser.ProfileLocked:
        sys.exit(3)
    sys.exit(0)


# --- browser discovery diagnostics ------------------------------------------
#
# "No browser is installed" and "Playwright is broken" call for opposite fixes,
# so doctor must be able to tell them apart.


def test_lookup_errors_start_empty_on_each_scan(monkeypatch):
    browser.LOOKUP_ERRORS["stale"] = "from a previous scan"
    monkeypatch.setattr(browser, "CHROME_CANDIDATES", {})
    monkeypatch.setattr(browser.shutil, "which", lambda _name: None)
    monkeypatch.setattr(browser, "_playwright_chromium_path", lambda: None)

    browser.find_browsers()

    assert "stale" not in browser.LOOKUP_ERRORS


def test_a_broken_playwright_records_why(monkeypatch, capsys, tmp_path):
    # Empty registry, so the driver fallback is the code under test.
    monkeypatch.setattr(browser, "_registry_root", lambda: tmp_path)

    class BrokenPlaywright:
        def __enter__(self):
            raise RuntimeError("Executable doesn't exist")

        def __exit__(self, *_args):
            return False

    fake_module = types.SimpleNamespace(sync_playwright=lambda: BrokenPlaywright())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    browser.LOOKUP_ERRORS.clear()

    assert browser._playwright_chromium_path() is None
    assert "playwright-chromium" in browser.LOOKUP_ERRORS
    assert "Executable doesn't exist" in browser.LOOKUP_ERRORS["playwright-chromium"]
    assert "lookup failed" in capsys.readouterr().err


def test_a_playwright_path_that_is_not_there_is_recorded(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(browser, "_registry_root", lambda: tmp_path)

    class Pw:
        chromium = types.SimpleNamespace(executable_path="/nope/chromium")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake_module = types.SimpleNamespace(sync_playwright=lambda: Pw())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    browser.LOOKUP_ERRORS.clear()

    assert browser._playwright_chromium_path() is None
    assert "does not exist" in browser.LOOKUP_ERRORS["playwright-chromium"]
    assert "not there" in capsys.readouterr().err


# --- Chromium discovery -----------------------------------------------------
#
# Asking Playwright for its executable path means starting Playwright: a driver
# subprocess and an event loop, just to learn a filename. It also warns loudly
# when anything else is already inside an asyncio loop. Discovery reads the
# registry directory instead, and only falls back to the driver.


def _install_chromium(root, build, platform="darwin"):
    relative = browser.CHROMIUM_RELATIVE_PATHS[platform][0]
    binary = root / f"chromium-{build}" / relative
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()
    return binary


def test_chromium_is_found_without_starting_playwright(monkeypatch, tmp_path):
    binary = _install_chromium(tmp_path, 1134)
    monkeypatch.setattr(browser, "_registry_root", lambda: tmp_path)
    monkeypatch.setattr(browser, "platform_key", lambda: "darwin")

    def explode():
        raise AssertionError("the driver must not be started for a lookup")

    monkeypatch.setattr(browser, "_chromium_from_driver", explode)

    assert browser._playwright_chromium_path() == str(binary)


def test_the_newest_build_wins_numerically(monkeypatch, tmp_path):
    # chromium-999 sorts above chromium-1187 as a string. It is not newer.
    _install_chromium(tmp_path, 999)
    newest = _install_chromium(tmp_path, 1187)
    monkeypatch.setattr(browser, "_registry_root", lambda: tmp_path)
    monkeypatch.setattr(browser, "platform_key", lambda: "darwin")

    assert browser._chromium_from_registry() == str(newest)


def test_an_empty_registry_finds_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "_registry_root", lambda: tmp_path)

    assert browser._chromium_from_registry() is None


def test_a_registry_entry_without_the_binary_is_not_a_find(monkeypatch, tmp_path):
    (tmp_path / "chromium-1134").mkdir()
    monkeypatch.setattr(browser, "_registry_root", lambda: tmp_path)
    monkeypatch.setattr(browser, "platform_key", lambda: "darwin")

    assert browser._chromium_from_registry() is None


def test_a_missing_registry_directory_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "_registry_root", lambda: tmp_path / "nope")
    browser.LOOKUP_ERRORS.clear()

    assert browser._chromium_from_registry() is None
    assert browser.LOOKUP_ERRORS == {}


def test_the_browsers_path_override_is_honored(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    assert browser._registry_root() == tmp_path


def test_the_zero_override_means_the_default_layout(monkeypatch):
    # PLAYWRIGHT_BROWSERS_PATH=0 means "next to the package", not a directory
    # named "0" -- treating it as a path would search a directory that is not
    # there and report no browser on a working install.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")

    assert browser._registry_root() != pathlib.Path("0")


def test_linux_looks_in_the_xdg_cache(monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(browser, "platform_key", lambda: "linux")

    assert browser._registry_root().parts[-2:] == (".cache", "ms-playwright")


def test_the_driver_is_the_fallback_for_an_unusual_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "_registry_root", lambda: tmp_path)
    monkeypatch.setattr(browser, "_chromium_from_driver", lambda: "/opt/odd/chromium")

    assert browser._playwright_chromium_path() == "/opt/odd/chromium"


# --- Same-origin fetch --------------------------------------------------------


def test_a_same_origin_fetch_runs_in_the_page(make_session):
    page = FakePage(url="https://www.walmart.com/")
    page.evaluate_result = {"status": 200, "body": '{"ok": true}'}
    session = make_session(page)

    result = session.fetch_same_origin(
        "https://www.walmart.com/orchestra/snb/graphql/Search/abc/search?variables=%7B%7D",
        headers={"X-APOLLO-OPERATION-NAME": "Search"},
    )

    assert result["status"] == 200
    url, headers = page.evaluated[0]
    assert url.endswith("variables=%7B%7D")
    assert headers["X-APOLLO-OPERATION-NAME"] == "Search"


def test_an_off_origin_fetch_is_refused(make_session):
    # This method exists to reuse the session's credentials. Sending those
    # somewhere else is the one thing it must never do.
    session = make_session(FakePage(url="https://www.walmart.com/"))

    with pytest.raises(safety.UnsafeNavigation):
        session.fetch_same_origin("https://evil.example.com/collect?c=1")


def test_a_lookalike_origin_is_refused(make_session):
    session = make_session(FakePage(url="https://www.walmart.com/"))

    with pytest.raises(safety.UnsafeNavigation):
        session.fetch_same_origin("https://www.walmart.com.evil.example/api")


def test_a_forbidden_url_is_refused_even_on_our_own_origin(make_session):
    session = make_session(FakePage(url="https://www.walmart.com/"))

    with pytest.raises(safety.UnsafeNavigation):
        session.fetch_same_origin("https://www.walmart.com/checkout")


def test_the_page_is_brought_on_origin_first(make_session):
    # A fetch with credentials:'same-origin' from about:blank sends no cookies
    # and reads as signed out.
    page = FakePage(url="about:blank")
    session = make_session(page)

    session.fetch_same_origin("https://www.walmart.com/orchestra/snb/graphql/Search/abc/search")

    assert page.navigations == [browser.WALMART_ORIGIN]


def test_an_on_origin_page_is_left_where_it_is(make_session):
    page = FakePage(url="https://www.walmart.com/cart")
    session = make_session(page)

    session.fetch_same_origin("https://www.walmart.com/orchestra/snb/graphql/Search/abc/search")

    assert page.navigations == []
