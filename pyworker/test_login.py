"""Login workflow tests against page doubles. Nothing here contacts Walmart."""

from __future__ import annotations

import io
import json
import time

import pytest

import login
import protocol
from conftest import FakePage

SIGNED_IN_SELECTOR = "a[href='/account']"
EMAIL_SELECTOR = "input#email"
PASSWORD_SELECTOR = "input#password"
SUBMIT_SELECTOR = "button[type='submit']"
PRESS_HOLD_SELECTOR = "text=Press & Hold"


def _deadline(seconds: float = 0.0) -> float:
    """A deadline that has already passed, so a challenge check does not wait."""
    return time.monotonic() + seconds


def test_detect_signed_in_reads_the_account_surface(make_session):
    page = FakePage(visible={SIGNED_IN_SELECTOR})
    session = make_session(page)

    assert login.detect_signed_in(session) == "link"
    assert page.navigations == [login.ACCOUNT_URL]


def test_detect_signed_in_returns_none_when_no_marker_matches(make_session):
    session = make_session(FakePage(visible=set()))

    assert login.detect_signed_in(session) is None


def test_left_signin_is_false_while_on_the_login_page():
    assert not login._left_signin(FakePage(url="https://www.walmart.com/account/login"))


def test_left_signin_is_false_when_signed_out_markers_persist():
    page = FakePage(url="https://www.walmart.com/", visible={"a[href*='/account/login']"})

    assert not login._left_signin(page)


def test_left_signin_is_true_once_the_flow_moves_on():
    assert login._left_signin(FakePage(url="https://www.walmart.com/", visible=set()))


def test_credentials_are_typed_but_never_logged(make_session, capsys):
    page = FakePage(visible={EMAIL_SELECTOR, PASSWORD_SELECTOR, SUBMIT_SELECTOR})
    session = make_session(page)

    filled = login._attempt_credential_fill(
        session, page, {"email": "eric@example.com", "password": "hunter2"}, _deadline()
    )

    assert filled is True
    assert (EMAIL_SELECTOR, "eric@example.com") in page.filled
    assert (PASSWORD_SELECTOR, "hunter2") in page.filled

    emitted = capsys.readouterr().out
    assert "hunter2" not in emitted
    assert "eric@example.com" not in emitted


def test_missing_credentials_do_not_attempt_a_fill(make_session):
    page = FakePage(visible={EMAIL_SELECTOR})
    session = make_session(page)

    assert login._attempt_credential_fill(session, page, {"email": "a@example.com"}, _deadline()) is False
    assert page.filled == []


def test_selector_drift_falls_back_to_manual_instead_of_hanging(make_session, capsys):
    page = FakePage(visible=set())
    session = make_session(page)

    creds = {"email": "a@example.com", "password": "p"}

    assert login._attempt_credential_fill(session, page, creds, _deadline()) is False
    assert "email field not found" in capsys.readouterr().out


# A submit button we failed to press looks exactly like a hang from the user's
# side, so the worker has to say it happened.
def test_an_unpressed_continue_button_warns(make_session, capsys):
    page = FakePage(visible={EMAIL_SELECTOR, PASSWORD_SELECTOR})
    session = make_session(page)

    creds = {"email": "a@example.com", "password": "p"}
    filled = login._attempt_credential_fill(session, page, creds, _deadline())
    emitted = capsys.readouterr().out

    assert filled is True
    assert "could not press Continue" in emitted
    assert "could not press Sign in" in emitted


def test_a_pressed_button_does_not_warn(make_session, capsys):
    page = FakePage(
        visible={EMAIL_SELECTOR, PASSWORD_SELECTOR, SUBMIT_SELECTOR},
        labels={SUBMIT_SELECTOR: "Continue"},
    )
    session = make_session(page)
    login._attempt_credential_fill(session, page, {"email": "a@example.com", "password": "p"}, _deadline())

    assert "could not press" not in capsys.readouterr().out


def test_session_status_reads_the_account_surface(make_session, monkeypatch):
    page = FakePage(visible={SIGNED_IN_SELECTOR})
    session = make_session(page)
    monkeypatch.setattr(login.browser, "open_session", _fake_open_session(session))

    status = login.session_status({"options": {"profile_dir": "/tmp/p"}})

    assert status["signed_in"] is True
    assert status["detected_by"] == "link"
    assert status["headless"] is True
    assert status["challenge"] is None


def test_session_status_reports_a_signed_out_session(make_session, monkeypatch):
    session = make_session(FakePage(visible=set()))
    monkeypatch.setattr(login.browser, "open_session", _fake_open_session(session))

    status = login.session_status({"options": {"profile_dir": "/tmp/p"}})

    assert status["signed_in"] is False
    assert status["detected_by"] is None


# A headless command that meets a challenge must name it, not open a window.
def test_session_status_names_a_challenge(make_session, monkeypatch):
    session = make_session(FakePage(visible={"text=Robot or human"}))
    monkeypatch.setattr(login.browser, "open_session", _fake_open_session(session))

    status = login.session_status({"options": {"profile_dir": "/tmp/p"}})

    assert status["signed_in"] is False
    assert status["challenge"] == "captcha"
    assert status["headless"] is True


def test_session_status_honors_a_headless_false_request(make_session, monkeypatch):
    session = make_session(FakePage(visible={SIGNED_IN_SELECTOR}))
    monkeypatch.setattr(login.browser, "open_session", _fake_open_session(session))

    status = login.session_status({"options": {"profile_dir": "/tmp/p", "headless": False}})

    assert status["headless"] is False


def test_wait_does_not_navigate_while_a_challenge_is_on_screen(make_session, monkeypatch):
    # An SMS code half-typed by the user must not be wiped out by a poll.
    page = FakePage(url="https://www.walmart.com/account/login", visible={"text=verification code"})
    session = make_session(page)
    monkeypatch.setattr(login.time, "sleep", lambda _s: None)

    clock = iter([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
    monkeypatch.setattr(login.time, "monotonic", lambda: next(clock))

    outcome = login._wait_for_session(session, wait_s=5, started=0)

    assert outcome.signed_in is False
    assert outcome.challenge == "otp"
    assert page.navigations == []


def test_wait_confirms_the_session_once_the_flow_leaves_sign_in(make_session, monkeypatch):
    page = FakePage(url="https://www.walmart.com/", visible={SIGNED_IN_SELECTOR})
    session = make_session(page)
    monkeypatch.setattr(login.time, "sleep", lambda _s: None)

    outcome = login._wait_for_session(session, wait_s=30, started=0)

    assert outcome.signed_in is True
    assert outcome.detected_by == "link"
    assert login.ACCOUNT_URL in page.navigations


def test_login_short_circuits_when_already_signed_in(make_session, monkeypatch):
    page = FakePage(visible={SIGNED_IN_SELECTOR})
    session = make_session(page)
    monkeypatch.setattr(login.browser, "open_session", _fake_open_session(session))

    result = login.login({"params": {}, "options": {"profile_dir": "/tmp/p"}, "secrets": {}})

    assert result["signed_in"] is True
    assert "already signed in" in result["notes"][0]
    assert login.SIGNIN_URL not in page.navigations


def test_login_forces_a_visible_window(make_session, monkeypatch):
    page = FakePage(visible={SIGNED_IN_SELECTOR})
    session = make_session(page)
    monkeypatch.setattr(login.browser, "open_session", _fake_open_session(session))

    login.login({"params": {}, "options": {"profile_dir": "/tmp/p", "headless": True}, "secrets": {}})

    assert session.options.headless is False


def test_login_reports_failure_when_the_wait_expires(make_session, monkeypatch):
    page = FakePage(url="https://www.walmart.com/account/login", visible=set())
    session = make_session(page)
    monkeypatch.setattr(login.browser, "open_session", _fake_open_session(session))
    monkeypatch.setattr(login.time, "sleep", lambda _s: None)

    result = login.login(
        {
            "params": {"manual": True, "wait_seconds": 0},
            "options": {"profile_dir": "/tmp/p"},
            "secrets": {},
        }
    )

    assert result["signed_in"] is False
    assert "no signed-in session" in result["notes"][0]


def test_outcome_serializes_without_secrets():
    outcome = login.LoginOutcome(signed_in=True, detected_by="link", used_stored_credentials=True)
    payload = json.loads(json.dumps(outcome.to_dict()))

    assert payload["signed_in"] is True
    assert set(payload) == {
        "signed_in",
        "detected_by",
        "challenge",
        "elapsed_s",
        "used_stored_credentials",
        "notes",
    }


def test_progress_messages_are_protocol_events():
    out = io.StringIO()
    protocol.progress("finish sign-in in the browser window", stream=out)
    event = json.loads(out.getvalue())

    assert event["type"] == "progress"


def _fake_open_session(session):
    from contextlib import contextmanager

    @contextmanager
    def opener(options):
        session.options = options
        yield session

    return opener


# --- browser closure vs selector drift -------------------------------------
#
# Both look like "nothing matched". They need opposite responses, so the
# distinction has to survive every path that polls the page.


def test_a_closed_page_is_reported_as_closed_not_as_a_missing_selector():
    page = FakePage(visible={SIGNED_IN_SELECTOR}, closed=True)

    with pytest.raises(login.BrowserClosed) as excinfo:
        login._first_match(page, login.SIGNED_IN_MARKERS)

    assert "closed" in str(excinfo.value)


def test_a_merely_missing_selector_is_still_data():
    page = FakePage(visible=set())

    assert login._first_match(page, login.SIGNED_IN_MARKERS) is None


def test_a_closed_page_stops_a_fill_instead_of_falling_back_to_manual():
    page = FakePage(visible={EMAIL_SELECTOR}, closed=True)

    with pytest.raises(login.BrowserClosed):
        login._fill_first(page, login.EMAIL_INPUT, "eric@example.com")


def test_a_closed_page_stops_a_click(make_session):
    page = FakePage(visible={SUBMIT_SELECTOR})
    session = make_session(page)
    page.closed = True

    with pytest.raises(login.BrowserClosed):
        login._click_first(session, page, login.SIGNIN_BUTTON)


def test_left_signin_reports_closure_rather_than_a_bare_false():
    page = FakePage(closed=True)

    # url is a plain attribute on the double, so force the read to fail the way
    # a dead page does.
    class Dead(FakePage):
        @property
        def url(self):
            raise RuntimeError("Target page, context or browser has been closed")

        @url.setter
        def url(self, value):
            pass

    with pytest.raises(login.BrowserClosed):
        login._left_signin(Dead(closed=True))
    assert page.is_closed() is True


def test_the_wait_loop_names_a_closed_browser_in_its_notes(make_session, monkeypatch):
    page = FakePage(url="https://www.walmart.com/account/login")
    session = make_session(page)
    monkeypatch.setattr(login.time, "sleep", lambda _s: None)
    page.closed = True

    outcome = login._wait_for_session(session, 30, login.time.monotonic())

    assert outcome.signed_in is False
    assert "browser closed" in outcome.notes[0]
    # The generic timeout wording would send the user hunting for selector drift.
    assert "no signed-in session" not in " ".join(outcome.notes)


def test_a_refused_click_is_loud_and_clicks_nothing(make_session, capsys):
    # An unreadable label means the click guard cannot verify the control.
    page = FakePage(visible={SUBMIT_SELECTOR}, unreadable={SUBMIT_SELECTOR})
    session = make_session(page)

    assert login._click_first(session, page, [SUBMIT_SELECTOR]) is False
    assert page.clicked == []
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert any(e.get("level") == "error" for e in events)


# --- --force ----------------------------------------------------------------


def test_force_signs_in_again_even_when_a_session_looks_valid(make_session, monkeypatch):
    page = FakePage(url="https://www.walmart.com/account", visible={SIGNED_IN_SELECTOR})
    session = make_session(page)
    monkeypatch.setattr(login.browser, "open_session", _fake_open_session(session))
    monkeypatch.setattr(login.time, "sleep", lambda _s: None)

    result = login.login(
        {
            "params": {"manual": True, "force": True, "wait_seconds": 0},
            "options": {"profile_dir": "/tmp/p"},
            "secrets": {},
        }
    )

    assert login.SIGNIN_URL in page.navigations, "--force must reopen the sign-in page"
    assert result["signed_in"] is False


def test_without_force_a_valid_session_short_circuits(make_session, monkeypatch):
    page = FakePage(url="https://www.walmart.com/account", visible={SIGNED_IN_SELECTOR})
    session = make_session(page)
    monkeypatch.setattr(login.browser, "open_session", _fake_open_session(session))

    result = login.login(
        {
            "params": {"manual": True, "wait_seconds": 0},
            "options": {"profile_dir": "/tmp/p"},
            "secrets": {},
        }
    )

    assert result["signed_in"] is True
    assert login.SIGNIN_URL not in page.navigations


# --- Human-verification challenges -----------------------------------------
#
# Observed live on 2026-08-26: a press-and-hold challenge on the very first page
# load, and a second one after clicking Sign In. No SMS code was requested. The
# tests below encode that sequence, because filling before checking is what
# turns "a human needs to hold a button" into "Walmart changed their markup".


def test_the_blocked_interstitial_is_recognized_by_url():
    page = FakePage(url="https://identity.walmart.com/blocked", visible=set())

    assert login._current_challenge(page) == "blocked-page"


def test_a_challenge_is_recognized_by_url_before_any_text_exists():
    # An interstitial has a URL before it has readable text.
    page = FakePage(url="https://identity.walmart.com/blocked?why=px", visible=set())

    assert login._current_challenge(page) is not None


def test_press_and_hold_is_recognized_by_text():
    page = FakePage(url="https://identity.walmart.com/account/login", visible={PRESS_HOLD_SELECTOR})

    assert login._current_challenge(page) == "press-hold"


def test_a_clear_page_reports_no_challenge():
    page = FakePage(url="https://www.walmart.com/account", visible=set())

    assert login._current_challenge(page) is None


def test_await_clear_returns_immediately_when_nothing_is_in_the_way():
    page = FakePage(url="https://www.walmart.com/account", visible=set())

    assert login._await_clear(page, _deadline(), "filling the email") is None


def test_await_clear_names_the_challenge_it_waited_on(capsys):
    page = FakePage(url="https://identity.walmart.com/blocked", visible=set())

    seen = login._await_clear(page, _deadline(), "filling the email")

    assert seen == "blocked-page"
    assert "waiting on a blocked-page challenge before filling the email" in capsys.readouterr().out


def test_await_clear_gives_up_at_the_deadline_rather_than_forever(capsys):
    page = FakePage(url="https://identity.walmart.com/blocked", visible=set())

    login._await_clear(page, _deadline(), "filling the email")

    assert "still on a blocked-page challenge when the wait expired" in capsys.readouterr().out


def test_a_field_behind_a_challenge_is_not_reported_as_selector_drift(make_session, capsys):
    # The bug this prevents: press-and-hold covers the form, the email lookup
    # times out, and we tell the user their selectors drifted.
    page = FakePage(url="https://identity.walmart.com/blocked", visible=set())
    session = make_session(page)

    filled = login._attempt_credential_fill(
        session, page, {"email": "a@example.com", "password": "p"}, _deadline()
    )

    emitted = capsys.readouterr().out

    assert filled is False
    assert "behind a blocked-page challenge" in emitted
    assert "falling back to manual entry" not in emitted


def test_a_genuinely_missing_field_still_reports_drift(make_session, capsys):
    page = FakePage(url="https://identity.walmart.com/account/login", visible=set())
    session = make_session(page)

    login._attempt_credential_fill(session, page, {"email": "a@example.com", "password": "p"}, _deadline())

    assert "email field not found" in capsys.readouterr().out


def test_nothing_is_typed_into_a_page_showing_a_challenge(make_session):
    page = FakePage(url="https://identity.walmart.com/blocked", visible={EMAIL_SELECTOR})
    session = make_session(page)

    login._attempt_credential_fill(session, page, {"email": "a@example.com", "password": "p"}, _deadline())

    assert page.filled == []


# --- Observed sign-in host --------------------------------------------------


def test_sign_in_uses_the_identity_host():
    # Observed: the login page is on identity.walmart.com, not www.
    assert login.SIGNIN_URL == "https://identity.walmart.com/account/login"
    assert "identity.walmart.com" in login.SIGNIN_URL


def test_the_signin_url_is_not_forbidden_by_the_navigation_guard():
    # A guard tightening that broke login would look like Walmart changing.
    import safety

    assert safety.is_forbidden_url(login.SIGNIN_URL) is False


# --- x-auth-status ----------------------------------------------------------


class RecordingSession:
    """Session double that lets a test replay response headers."""

    def __init__(self, page):
        self.page = page
        self.handlers = []

    def on_response(self, handler):
        self.handlers.append(handler)

    def emit(self, headers):
        class Response:
            def __init__(self, headers):
                self.headers = headers

        for handler in self.handlers:
            handler(Response(headers))


def test_the_auth_status_header_is_observed_passively():
    session = RecordingSession(FakePage())
    state = login._auth_status_watcher(session)

    assert state["passed"] is False
    session.emit({"x-auth-status": "passed"})
    assert state["passed"] is True


def test_a_repeated_auth_status_header_still_reads_as_passed():
    # Observed live as "passed, passed, passed" when several calls coalesce.
    session = RecordingSession(FakePage())
    state = login._auth_status_watcher(session)

    session.emit({"x-auth-status": "passed, passed, passed"})

    assert state["passed"] is True


def test_an_unauthenticated_response_does_not_claim_a_session():
    session = RecordingSession(FakePage())
    state = login._auth_status_watcher(session)

    session.emit({"x-auth-status": "failed"})
    session.emit({"content-type": "application/json"})

    assert state["passed"] is False


def test_unreadable_headers_prove_nothing():
    class Hostile:
        @property
        def headers(self):
            raise RuntimeError("gone")

    session = RecordingSession(FakePage())
    state = login._auth_status_watcher(session)
    for handler in session.handlers:
        handler(Hostile())

    assert state["passed"] is False
