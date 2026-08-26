"""Visible sign-in against the dedicated persistent profile.

Design constraints:
  * login is the only command that opens a window;
  * credentials arrive on stdin and are typed into the page, never logged;
  * sign-in is gated by human verification challenges (observed live: two
    press-and-hold challenges, one on first page load and one on clicking Sign
    In). The human clears them in the visible window. We never solve, simulate,
    or evade a challenge - we wait, and we say what we are waiting for;
  * an SMS one-time code may also appear. It did not on the observed run, so it
    is handled as a possibility rather than as the expected path;
  * success is proven by re-reading an authenticated surface, not by the
    absence of an error.

Every selector below is UNVERIFIED until live reconnaissance confirms it, and
each is tried as a list of candidates so drift shows up as "no candidate
matched" instead of a silent hang. `detected_by` is reported so we learn which
candidate is actually load-bearing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import browser
import protocol
import safety


class BrowserClosed(RuntimeError):
    """The page or browser went away while we were working with it.

    A crashed or closed browser and a drifted selector produce the same
    symptom - nothing matches - but they need completely different responses.
    Conflating them sends the user hunting for Walmart UI changes when the
    window simply died, so this is raised and reported distinctly.
    """


def _page_gone(page) -> bool:
    """True when the page reports itself closed.

    An object without is_closed() is not evidence of anything, so it counts as
    alive; a page that raises when asked is not answering, so it counts as gone.
    """
    probe = getattr(page, "is_closed", None)
    if probe is None:
        return False
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001 - a page too broken to answer counts as gone
        return True


def _closure_error(page, exc: Exception) -> bool:
    """Does this failure mean the page/browser is gone rather than absent?"""
    if _page_gone(page):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        marker in text
        for marker in (
            "target closed",
            "target page, context or browser has been closed",
            "browser has been closed",
            "page has been closed",
            "crashed",
        )
    )


ACCOUNT_URL = f"{browser.WALMART_ORIGIN}/account"
# Observed live: sign-in lives on identity.walmart.com, not www. Walmart also
# parks bot-challenge interstitials on that host (see CHALLENGE_URLS).
IDENTITY_ORIGIN = "https://identity.walmart.com"
SIGNIN_URL = f"{IDENTITY_ORIGIN}/account/login"

# Header Walmart returns on authenticated API responses. Observed 38 times in
# the first live capture, always as "passed". This is stronger evidence than any
# DOM text: it survives redesigns, localization, and A/B tests.
AUTH_STATUS_HEADER = "x-auth-status"
AUTH_STATUS_PASSED = "passed"

# How often we look again while a human is working in the window.
POLL_SECONDS = 3

# --- RECON REQUIRED: candidates below are hypotheses, not confirmed contracts.
SIGNED_IN_MARKERS = [
    ("link", "a[href='/account']"),
    ("account-heading", "h1:has-text('Account')"),
    ("sign-out", "text=Sign out"),
]
SIGNED_OUT_MARKERS = [
    ("signin-heading", "text=Sign in"),
    ("signin-link", "a[href*='/account/login']"),
]
EMAIL_INPUT = ["input#email", "input[name='email']", "input[type='email']"]
PASSWORD_INPUT = ["input#password", "input[name='password']", "input[type='password']"]
CONTINUE_BUTTON = ["button[type='submit']", "button:has-text('Continue')"]
SIGNIN_BUTTON = ["button[type='submit']", "button:has-text('Sign in')"]
CHALLENGE_MARKERS = [
    ("otp", "text=verification code"),
    ("otp-alt", "text=one-time"),
    ("captcha", "text=Robot or human"),
    ("press-hold", "text=Press & Hold"),
    ("press-hold-alt", "text=Press and Hold"),
]

# URL substrings that mean "a challenge is on screen", checked before any text
# marker. Observed live: identity.walmart.com/blocked is served as a normal 200
# document ahead of the login page. A URL is a far more reliable signal than
# page text, which changes with every redesign and every locale.
CHALLENGE_URLS = [
    ("blocked-page", "identity.walmart.com/blocked"),
    ("blocked-path", "/blocked"),
]


@dataclass
class LoginOutcome:
    signed_in: bool
    detected_by: str | None = None
    challenge: str | None = None
    elapsed_s: float = 0.0
    used_stored_credentials: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signed_in": self.signed_in,
            "detected_by": self.detected_by,
            "challenge": self.challenge,
            "elapsed_s": round(self.elapsed_s, 1),
            "used_stored_credentials": self.used_stored_credentials,
            "notes": self.notes,
        }


def _first_match(page, candidates: list[tuple[str, str]], timeout_ms: int = 1500) -> str | None:
    """Return the name of the first candidate selector that is visible.

    Raises BrowserClosed if the page died; a missing selector is still data.
    """
    for name, selector in candidates:
        try:
            if page.locator(selector).first.is_visible(timeout=timeout_ms):
                return name
        except Exception as exc:  # noqa: BLE001 - a missing selector is data, not an error
            if _closure_error(page, exc):
                raise BrowserClosed(f"browser closed while checking {name}: {exc}") from exc
            continue
    return None


def _fill_first(page, candidates: list[str], value: str, timeout_ms: int = 5000) -> bool:
    """Type a value into the first candidate that exists. Never logs the value."""
    for selector in candidates:
        try:
            locator = page.locator(selector).first
            locator.wait_for(state="visible", timeout=timeout_ms)
            locator.fill(value)
            return True
        except Exception as exc:  # noqa: BLE001 - a missing field is data
            if _closure_error(page, exc):
                raise BrowserClosed(f"browser closed while filling a field: {exc}") from exc
            continue
    return False


def _click_first(session, page, candidates: list[str], timeout_ms: int = 5000) -> bool:
    """Click the first candidate that exists. Returns whether anything was clicked."""
    for selector in candidates:
        try:
            locator = page.locator(selector).first
            locator.wait_for(state="visible", timeout=timeout_ms)
            session.click(locator)
            return True
        except safety.UnsafeNavigation as exc:
            # The click guard refused. That is a decision, not a miss: no click
            # happened, we do not retry the next candidate, and the human is
            # told so they can finish in the window they are already looking at.
            protocol.log(f"refused to click a candidate control: {exc}", level="error")
            return False
        except Exception as exc:  # noqa: BLE001 - a missing button is data
            if _closure_error(page, exc):
                raise BrowserClosed(f"browser closed while clicking: {exc}") from exc
            continue
    return False


def _left_signin(page) -> bool:
    """True once the browser is no longer sitting on the sign-in screen."""
    try:
        url = page.url or ""
    except Exception as exc:  # noqa: BLE001 - a page mid-navigation is not an error
        if _closure_error(page, exc):
            raise BrowserClosed(f"browser closed while reading the URL: {exc}") from exc
        return False
    if "/account/login" in url or "/login" in url:
        return False
    return _first_match(page, SIGNED_OUT_MARKERS, timeout_ms=300) is None


def _current_challenge(page) -> str | None:
    """Name the challenge on screen, or None.

    URL first: an interstitial has a URL before it has readable text, and the
    URL keeps meaning the same thing after a redesign.
    """
    try:
        url = (page.url or "").lower()
    except Exception as exc:  # noqa: BLE001
        if _closure_error(page, exc):
            raise BrowserClosed(f"browser closed while reading the URL: {exc}") from exc
        url = ""

    for name, fragment in CHALLENGE_URLS:
        if fragment in url:
            return name

    return _first_match(page, CHALLENGE_MARKERS, timeout_ms=500)


def _await_clear(page, deadline: float, what: str) -> str | None:
    """Wait for any challenge to clear before we touch the page.

    This is the sequencing lesson from the first live sign-in: a press-and-hold
    challenge appears on the very first page load and again on clicking Sign In.
    Filling before checking makes the email field look missing, and we would
    have reported "email field not found" - blaming selector drift for a button
    a human simply had not held yet.

    Returns the challenge that was seen (None if the page was clear), so the
    caller can report what happened rather than only that it waited.
    """
    seen: str | None = None
    announced: set[str] = set()

    while True:
        challenge = _current_challenge(page)
        if not challenge:
            return seen

        seen = challenge
        if challenge not in announced:
            announced.add(challenge)
            protocol.progress(
                f"waiting on a {challenge} challenge before {what}; clear it in the browser window"
            )

        if time.monotonic() >= deadline:
            protocol.log(f"still on a {challenge} challenge when the wait expired", level="warn")
            return seen

        time.sleep(POLL_SECONDS)


def _blocked(page, deadline: float, what: str) -> str | None:
    """Wait for a challenge to clear, then report whatever is still in the way.

    Waiting is not the same as being allowed to proceed. The first version of
    this pass waited and then typed anyway, which meant a press-and-hold that
    outlived the wait still got credentials fired at it and Continue pressed
    against a wall.
    """
    _await_clear(page, deadline, what)
    return _current_challenge(page)


def _auth_status_watcher(session) -> dict[str, bool]:
    """Passively record whether Walmart ever tells us the session is good.

    Cheaper and more honest than a DOM guess: the site states it in a response
    header on every authenticated API call.
    """
    state = {"passed": False}

    def observe(response) -> None:
        try:
            value = (response.headers or {}).get(AUTH_STATUS_HEADER, "")
        except Exception:  # noqa: BLE001 - a header we cannot read proves nothing
            return
        if AUTH_STATUS_PASSED in value.lower():
            state["passed"] = True

    session.on_response(observe)
    return state


def detect_signed_in(session) -> str | None:
    """Authoritative check: load the account surface and look for proof."""
    page = session.page
    session.goto(ACCOUNT_URL, wait_until="domcontentloaded")
    marker = _first_match(page, SIGNED_IN_MARKERS, timeout_ms=4000)
    if marker:
        return marker
    return None


def session_status(request: dict[str, Any]) -> dict[str, Any]:
    """Read-only: is the stored session still usable? Headless by default."""
    options = browser.BrowserOptions.from_request(request.get("options", {}))
    with browser.open_session(options) as session:
        auth = _auth_status_watcher(session)
        marker = detect_signed_in(session)
        challenge = _current_challenge(session.page)
        if not marker and auth["passed"]:
            marker = AUTH_STATUS_HEADER
        return {
            "signed_in": bool(marker),
            "detected_by": marker,
            "challenge": challenge,
            "auth_status_passed": auth["passed"],
            "headless": options.headless,
        }


def login(request: dict[str, Any]) -> dict[str, Any]:
    """Visible sign-in. Returns only after the session is proven, or times out."""
    params = request.get("params", {}) or {}
    # Login is always visible; that is stated here, at construction, rather than
    # by mutating a shared options object that someone could later reuse.
    options = browser.BrowserOptions.from_request(request.get("options", {}), headless=False)
    secrets = request.get("secrets") or {}
    manual = bool(params.get("manual")) or not secrets
    force = bool(params.get("force"))
    # An explicit None means "use the default"; a deliberate 0 must stay 0.
    # `or 300` would turn an immediate timeout into a five-minute one
    # (pinned by test_login_reports_failure_when_the_wait_expires).
    wait_raw = params.get("wait_seconds")
    wait_s = 300 if wait_raw is None else int(wait_raw)

    started = time.monotonic()
    deadline = started + wait_s
    with browser.open_session(options) as session:
        auth = _auth_status_watcher(session)
        if not force:
            marker = detect_signed_in(session)
            if marker:
                return LoginOutcome(
                    signed_in=True,
                    detected_by=marker,
                    elapsed_s=time.monotonic() - started,
                    notes=["already signed in; pass --force to sign in again"],
                ).to_dict()

        protocol.progress("opening Walmart sign-in in a visible window")
        session.goto(SIGNIN_URL, wait_until="domcontentloaded")
        page = session.page

        # Challenge #1 is observed here, on the first load, before any field
        # exists to fill.
        gate = _await_clear(page, deadline, "the sign-in page loads")

        used_credentials = False
        if not manual:
            used_credentials = _attempt_credential_fill(session, page, secrets, deadline)

        protocol.progress(
            "finish sign-in in the browser window; clear any human-verification "
            "challenge there, and type a texted code there if one is asked for"
        )

        outcome = _wait_for_session(session, wait_s, started, auth)
        outcome.used_stored_credentials = used_credentials
        outcome.challenge = outcome.challenge or gate
        return outcome.to_dict()


def _attempt_credential_fill(session, page, secrets: dict[str, str], deadline: float) -> bool:
    """Type stored credentials. Returns whether both fields were filled.

    Every step waits for any challenge to clear first. Observed live, a
    press-and-hold sits in front of the form on load and another appears on
    clicking Sign In; without the wait, both read as "field not found" and we
    would blame Walmart's markup for a button nobody had held yet.

    Every click result is checked. A submit button we failed to press looks
    exactly like a hang from the user's side, so it has to announce itself.
    """
    email = secrets.get("email")
    password = secrets.get("password")
    if not email or not password:
        return False

    protocol.progress("filling stored credentials")
    if blocking := _blocked(page, deadline, "filling the email"):
        protocol.log(
            f"not typing credentials behind a {blocking} challenge; finish sign-in in the window",
            level="warn",
        )
        return False
    if not _fill_first(page, EMAIL_INPUT, email):
        protocol.log(_missing_field_message("email", page), level="warn")
        return False
    if not _click_first(session, page, CONTINUE_BUTTON, timeout_ms=3000):
        protocol.log(
            "could not press Continue after the email; finish sign-in in the window",
            level="warn",
        )

    # Challenge #2 is observed right here, after the first submit.
    if blocking := _blocked(page, deadline, "filling the password"):
        protocol.log(
            f"not typing the password behind a {blocking} challenge; finish sign-in in the window",
            level="warn",
        )
        return False
    if not _fill_first(page, PASSWORD_INPUT, password, timeout_ms=8000):
        protocol.log(_missing_field_message("password", page), level="warn")
        return False
    if not _click_first(session, page, SIGNIN_BUTTON, timeout_ms=3000):
        protocol.log(
            "could not press Sign in after the password; finish sign-in in the window",
            level="warn",
        )
    _await_clear(page, deadline, "the session is established")
    return True


def _missing_field_message(field: str, page) -> str:
    """Never report drift when a challenge is the actual explanation."""
    challenge = _current_challenge(page)
    if challenge:
        return f"{field} field not reachable behind a {challenge} challenge; finish it in the window"
    return f"{field} field not found; falling back to manual entry"


def _wait_for_session(
    session, wait_s: int, started: float, auth: dict[str, bool] | None = None
) -> LoginOutcome:
    """Poll for proof of a session while the human clears whatever is in the way.

    Deliberately passive: navigating during the wait would wipe out a half-held
    press-and-hold or a half-typed code. We watch the page the user is on, and
    only navigate to the account surface once the flow has left the sign-in
    screen.
    """
    auth = auth if auth is not None else {"passed": False}
    deadline = time.monotonic() + wait_s
    last_challenge: str | None = None
    announced: set[str] = set()

    while time.monotonic() < deadline:
        page = session.page
        try:
            challenge = _current_challenge(page)
            if challenge and challenge not in announced:
                announced.add(challenge)
                last_challenge = challenge
                protocol.progress(f"waiting on a {challenge} challenge in the browser window")

            if not challenge and _left_signin(page):
                marker = detect_signed_in(session) or (AUTH_STATUS_HEADER if auth["passed"] else None)
                if marker:
                    return LoginOutcome(
                        signed_in=True,
                        detected_by=marker,
                        challenge=last_challenge,
                        elapsed_s=time.monotonic() - started,
                    )
        except BrowserClosed as exc:
            # Say what happened. "No signed-in session after 300s" would send
            # the user looking for selector drift that is not there.
            protocol.log(f"{exc}", level="error")
            return LoginOutcome(
                signed_in=False,
                challenge=last_challenge,
                elapsed_s=time.monotonic() - started,
                notes=[f"browser closed before sign-in completed: {exc}"],
            )
        time.sleep(POLL_SECONDS)

    return LoginOutcome(
        signed_in=False,
        challenge=last_challenge,
        elapsed_s=time.monotonic() - started,
        notes=[f"no signed-in session after {wait_s}s"],
    )
