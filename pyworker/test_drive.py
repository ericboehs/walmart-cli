"""The driver: the one tool here that clicks.

Everything in this file is about what it refuses to do, because the thing it
does is a real write to a real cart and the only defence is that it stops on
anything it does not recognise. There is no live test. There cannot be: the
cart DOM has never been recorded, which is itself why every selector in the
script is treated as a guess that must match exactly once.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pyworker"))

import safety  # noqa: E402


def _driver():
    path = ROOT / "scripts" / "drive-cart-mutation.py"
    spec = importlib.util.spec_from_file_location("drive_cart_mutation", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("drive_cart_mutation", module)
    spec.loader.exec_module(module)
    return module


class FakeLocator:
    """One selector's worth of matches, and a record of what was clicked."""

    def __init__(self, page, count=1, label=None, buttons=None):
        self.page = page
        self._count = count
        self._label = label
        self._buttons = buttons or {}

    def count(self):
        return self._count

    def get_by_role(self, role, name=None):
        assert role == "button"
        found = self._buttons.get(name)
        if found is None:
            return FakeLocator(self.page, count=0)
        return found

    def get_attribute(self, name):
        return self._label if name == "aria-label" else None

    def click(self):
        self.page.clicked.append(self._label)


class FakePage:
    """A cart page that answers selectors from a dict and remembers clicks."""

    def __init__(self, lines=None, buttons=None):
        self.lines = lines if lines is not None else {}
        self.buttons = buttons or {}
        self.clicked = []
        self.goto_urls = []
        self.waits = []

    def locator(self, selector):
        count = self.lines.get(selector, 0)
        return FakeLocator(self, count=count, buttons=self.buttons)

    def goto(self, url, **_kwargs):
        self.goto_urls.append(url)

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


def button(page, label):
    return FakeLocator(page, count=1, label=label)


def cart_with_one_line(item_id="33282303", labels=("Increase quantity", "Decrease quantity")):
    page = FakePage(lines={f'[data-item-id="{item_id}"]': 1})
    page.buttons = {}
    for label in labels:
        page.buttons[label.split()[0].lower()] = button(page, label)
    return page


# --- finding the line -----------------------------------------------------------------


def test_a_line_that_matches_once_is_the_line():
    driver = _driver()
    page = cart_with_one_line()

    _line, selector = driver.find_line(page, "33282303")

    assert selector == '[data-item-id="33282303"]'


def test_a_cart_with_no_matching_line_is_refused_not_guessed():
    driver = _driver()

    with pytest.raises(driver.Refused) as exc:
        driver.find_line(FakePage(), "33282303")

    assert "never been recorded" in str(exc.value), "the refusal should say the selectors are guesses"


def test_two_matching_lines_are_worse_than_none():
    """Picking the first would report a change to whichever line that was."""
    driver = _driver()
    page = FakePage(lines={'[data-item-id="33282303"]': 2})

    with pytest.raises(driver.Refused) as exc:
        driver.find_line(page, "33282303")

    assert "cannot say which line" in str(exc.value)


def test_later_selectors_are_tried_when_earlier_ones_miss():
    driver = _driver()
    page = FakePage(lines={'a[href*="/ip/33282303"]': 1})

    _line, selector = driver.find_line(page, "33282303")

    assert selector == 'a[href*="/ip/33282303"]'


# --- the click gate -------------------------------------------------------------------


def test_a_control_walmart_relabelled_check_out_is_never_clicked():
    """The name comes off the page, so it is not ours to trust.

    This is the one that matters. Everything else in this file protects the
    cart; this protects the account.
    """
    driver = _driver()
    page = cart_with_one_line()
    page.buttons["decrease"] = button(page, "Check out now")
    line, _ = driver.find_line(page, "33282303")

    with pytest.raises(safety.UnsafeNavigation):
        driver.find_control(page, line, -1)

    assert page.clicked == []


def test_a_line_with_two_matching_buttons_is_refused():
    driver = _driver()
    page = cart_with_one_line()
    page.buttons["decrease"] = FakeLocator(page, count=2, label="Decrease quantity")
    line, _ = driver.find_line(page, "33282303")

    with pytest.raises(driver.Refused):
        driver.find_control(page, line, -1)


def test_a_line_with_no_quantity_control_is_refused():
    driver = _driver()
    page = cart_with_one_line(labels=())
    line, _ = driver.find_line(page, "33282303")

    with pytest.raises(driver.Refused):
        driver.find_control(page, line, 1)


@pytest.mark.parametrize("delta", [0, 2, -2, 10])
def test_only_one_step_at_a_time(delta):
    """A stepper clicked five times is five mutations, and this asked for one."""
    driver = _driver()

    with pytest.raises(driver.Refused):
        driver.click_once(cart_with_one_line(), "33282303", delta)


# --- ending where it started ----------------------------------------------------------


class FakeRecorder:
    def __init__(self, pending=0):
        self.pending = pending
        self.mutations = 0
        self.responses = []


def no_settle(_page, _recorder, seconds=None):
    return 0


def test_the_quantity_goes_back_up_after_it_goes_down():
    """A capture that leaves the cart different is a tool nobody runs twice."""
    driver = _driver()
    page = cart_with_one_line()

    notes = driver.drive(page, FakeRecorder(), "33282303", -1, no_settle)

    assert page.clicked == ["Decrease quantity", "Increase quantity"]
    assert any("restored" in note for note in notes)


def test_the_cart_is_restored_even_when_waiting_for_the_answer_fails():
    """The interesting failure is the one after the first click."""
    driver = _driver()
    page = cart_with_one_line()

    def explode(_page, _recorder, seconds=None):
        raise RuntimeError("the browser went away")

    with pytest.raises(RuntimeError):
        driver.drive(page, FakeRecorder(), "33282303", -1, explode)

    assert page.clicked == ["Decrease quantity", "Increase quantity"], "the cart was left changed"


def test_nothing_is_restored_when_nothing_was_clicked():
    """A refusal before the click must not click anything to 'undo' it.

    The first version of this used an empty page, where the restore would fail
    to find the line anyway -- so it passed whether or not the restore was
    guarded, and a mutant that always restored survived it. This one gives the
    restore a button it could really press: the line is there and the decrease
    control is missing, so a drive that refuses and then "undoes" itself would
    add one to a cart nobody had changed.
    """
    driver = _driver()
    page = cart_with_one_line(labels=("Increase quantity",))

    with pytest.raises(driver.Refused):
        driver.drive(page, FakeRecorder(), "33282303", -1, no_settle)

    assert page.clicked == [], "it undid a click it never made"


def test_a_refusal_before_any_click_on_a_page_with_nothing_to_click():
    driver = _driver()
    page = FakePage()

    with pytest.raises(driver.Refused):
        driver.drive(page, FakeRecorder(), "33282303", -1, no_settle)

    assert page.clicked == []


def test_a_failed_restore_is_recorded_rather_than_raised():
    """By then the cart is already changed; losing that to a traceback is worse."""
    driver = _driver()
    page = cart_with_one_line(labels=("Decrease quantity",))

    notes = driver.drive(page, FakeRecorder(), "33282303", -1, no_settle)

    assert page.clicked == ["Decrease quantity"]
    assert any("RESTORE FAILED" in note for note in notes)
    assert any("Fix it by hand" in note for note in notes)


def test_an_unanswered_request_is_noted():
    driver = _driver()

    def never_answers(_page, _recorder, seconds=None):
        return 1

    notes = driver.drive(cart_with_one_line(), FakeRecorder(), "33282303", -1, never_answers)

    assert any("unanswered" in note for note in notes)


# --- consent and exit codes -----------------------------------------------------------


def test_declining_the_prompt_clicks_nothing(tmp_path, monkeypatch, capsys):
    driver = _driver()
    opened = []
    monkeypatch.setattr(driver.browser, "BrowserOptions", lambda **k: object())
    monkeypatch.setattr(driver.browser, "open_session", lambda _o: opened.append(1))
    args = argparse.Namespace(
        item_id="33282303",
        delta=-1,
        out=str(tmp_path),
        profile=str(tmp_path),
        cdp_url="http://127.0.0.1:9222",
        yes=False,
    )
    monkeypatch.setattr("builtins.input", lambda _p: "y")

    assert driver.run(args) == 2
    assert opened == [], "a browser was opened after the prompt was declined"


def test_the_prompt_wants_the_word_not_a_letter():
    driver = _driver()

    assert driver.confirm("33282303", -1, read_line=lambda _p: "yes")
    assert driver.confirm("33282303", -1, read_line=lambda _p: " YES ")
    assert not driver.confirm("33282303", -1, read_line=lambda _p: "y")
    assert not driver.confirm("33282303", -1, read_line=lambda _p: "")


def test_the_prompt_says_the_cart_will_change():
    driver = _driver()
    said = []
    driver.confirm("33282303", -1, read_line=lambda prompt: said.append(prompt) or "no")

    assert "change your cart" in said[0]
    assert "33282303" in said[0]


def test_an_unavailable_browser_exits_two_and_writes_what_it_knows(tmp_path, monkeypatch):
    driver = _driver()

    def unavailable(_options):
        raise driver.browser.BrowserUnavailable("no Chrome on 9222")

    monkeypatch.setattr(driver.browser, "BrowserOptions", lambda **k: object())
    monkeypatch.setattr(driver.browser, "open_session", unavailable)
    args = argparse.Namespace(
        item_id="33282303",
        delta=-1,
        out=str(tmp_path),
        profile=str(tmp_path),
        cdp_url="http://127.0.0.1:9222",
        yes=True,
    )

    assert driver.run(args) == 2
    written = json.loads((tmp_path / "capture.json").read_text())
    assert any("browser unavailable" in note for note in written["notes"])


def test_a_run_that_clicked_but_learned_nothing_exits_nonzero(tmp_path, monkeypatch):
    """The whole point is the response. Clicking without one is a failed run."""
    driver = _driver()
    page = cart_with_one_line()

    class Session:
        def __init__(self):
            self.page = page

        def on_worker(self, handler):
            pass

        def on_request(self, handler):
            pass

        def on_response(self, handler):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(driver.browser, "BrowserOptions", lambda **k: object())
    monkeypatch.setattr(driver.browser, "open_session", lambda _o: Session())
    monkeypatch.setattr(_driver_capture(), "settle", no_settle, raising=False)
    args = argparse.Namespace(
        item_id="33282303",
        delta=-1,
        out=str(tmp_path),
        profile=str(tmp_path),
        cdp_url="http://127.0.0.1:9222",
        yes=True,
    )

    assert driver.run(args) != 0


def _driver_capture():
    return _driver().capture_module()


# --- the only page it ever opens ------------------------------------------------------


def test_the_cart_is_the_only_destination():
    """Gated even though it is a constant: the guard belongs on the acting path."""
    driver = _driver()

    assert driver.CART_URL == "https://www.walmart.com/cart"
    assert safety.assert_navigable(driver.CART_URL) == driver.CART_URL


def test_the_driver_names_no_order_flow():
    source = (ROOT / "scripts" / "drive-cart-mutation.py").read_text()

    for line in source.splitlines():
        if "FORBIDDEN" in line or "assert_clickable" in line or "safety." in line:
            continue
        assert not safety.is_forbidden_url(line.lower()), line


def test_the_navigation_gate_is_on_the_path_that_acts(tmp_path, monkeypatch):
    """Not a hypothetical: point the constant somewhere forbidden and see.

    CART_URL is a constant and always safe, so `assert_navigable(CART_URL)`
    could be deleted without any test noticing -- which is how a guard ends up
    written but not wired in. Moving the constant is the only way to ask
    whether the gate is really on the path that navigates.
    """
    driver = _driver()
    page = cart_with_one_line()

    class Session:
        def __init__(self):
            self.page = page

        def on_worker(self, handler):
            pass

        def on_request(self, handler):
            pass

        def on_response(self, handler):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(driver.browser, "BrowserOptions", lambda **k: object())
    monkeypatch.setattr(driver.browser, "open_session", lambda _o: Session())
    monkeypatch.setattr(
        driver, "CART_URL", f"{driver.browser.WALMART_ORIGIN}{safety.FORBIDDEN_URL_FRAGMENTS[0]}"
    )
    args = argparse.Namespace(
        item_id="33282303",
        delta=-1,
        out=str(tmp_path),
        profile=str(tmp_path),
        cdp_url="http://127.0.0.1:9222",
        yes=True,
    )

    assert driver.run(args) == 2
    assert page.goto_urls == [], "it navigated to a forbidden destination"
    assert page.clicked == []
