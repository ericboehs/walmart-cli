"""Dispatcher tests. Nothing here launches a browser or touches the network."""

from __future__ import annotations

import io
import json

import pytest

import browser
import challenge
import fetch
import protocol
import safety
import search


def run_main(request: dict, monkeypatch, capsys) -> tuple[int, list[dict]]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request) + "\n"))
    code = fetch.main([])
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.splitlines() if line.strip()]
    return code, events


def test_probe_reports_the_local_environment(monkeypatch, capsys):
    monkeypatch.setattr(browser, "playwright_version", lambda: "1.48.0")
    monkeypatch.setattr(browser, "find_browsers", lambda: {"chrome": "/x/chrome"})

    code, events = run_main(
        {"action": "probe", "options": {"profile_dir": "/nonexistent"}}, monkeypatch, capsys
    )

    assert code == 0
    data = events[-1]["data"]
    assert data["protocol"] == protocol.PROTOCOL_VERSION
    assert data["playwright"] == "1.48.0"
    assert data["profile_exists"] is False
    assert "cart.show" in data["actions"]


def test_probe_never_opens_a_browser(monkeypatch, capsys):
    def explode(*_args, **_kwargs):
        raise AssertionError("probe must not launch a browser")

    monkeypatch.setattr(browser, "open_session", explode)
    monkeypatch.setattr(browser, "playwright_version", lambda: None)
    monkeypatch.setattr(browser, "find_browsers", lambda: {})

    code, _events = run_main({"action": "probe", "options": {}}, monkeypatch, capsys)
    assert code == 0


def test_unknown_action_is_a_usage_error(monkeypatch, capsys):
    code, events = run_main({"action": "cart.place"}, monkeypatch, capsys)

    assert code == 2
    assert events[-1]["code"] == "unsupported_action"


# A KeyError inside a handler used to be indistinguishable from an unknown
# action, which turned a real bug into a confident, wrong diagnosis.
def test_a_key_error_inside_a_handler_is_an_internal_error(monkeypatch, capsys):
    def handler(_request):
        raise KeyError("itemId")

    monkeypatch.setitem(fetch.ACTIONS, "cart.show", handler)
    code, events = run_main({"action": "cart.show"}, monkeypatch, capsys)

    assert code == 1
    assert events[-1]["code"] == "internal"
    assert "KeyError" in events[-1]["message"]


def test_a_key_error_inside_a_mutation_handler_is_unverified(monkeypatch, capsys):
    def handler(_request):
        raise KeyError("cartLineId")

    monkeypatch.setitem(fetch.ACTIONS, "cart.add", handler)
    code, events = run_main({"action": "cart.add"}, monkeypatch, capsys)

    assert code == 3
    assert events[-1]["unverified"] is True


def test_a_protocol_version_mismatch_is_a_bad_request(monkeypatch, capsys):
    code, events = run_main({"protocol": 99, "action": "probe"}, monkeypatch, capsys)

    assert code == 2
    assert events[-1]["code"] == "bad_request"
    assert "out of sync" in events[-1]["message"]


def test_unimplemented_action_says_so_clearly(monkeypatch, capsys):
    code, events = run_main({"action": "my_items.list"}, monkeypatch, capsys)

    assert code == 2
    assert events[-1]["code"] == "not_implemented"
    assert "reconnaissance" in events[-1]["message"]


def test_bad_request_is_reported_not_crashed(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json\n"))
    code = fetch.main([])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]

    assert code == 2
    assert events[-1]["code"] == "bad_request"


def test_profile_contention_is_reported_rather_than_corrupting_state(monkeypatch, capsys):
    def locked(_request):
        raise browser.ProfileLocked("another walmart command is using the browser profile")

    monkeypatch.setitem(fetch.ACTIONS, "cart.show", locked)
    code, events = run_main({"action": "cart.show"}, monkeypatch, capsys)

    assert code == 1
    assert events[-1]["code"] == "profile_locked"


def test_a_crash_during_a_mutation_is_reported_as_unverified(monkeypatch, capsys):
    def boom(_request):
        raise RuntimeError("connection reset")

    monkeypatch.setitem(fetch.ACTIONS, "cart.add", boom)
    code, events = run_main({"action": "cart.add"}, monkeypatch, capsys)

    assert code == 3
    assert events[-1]["unverified"] is True


def test_a_crash_during_a_read_is_a_plain_failure(monkeypatch, capsys):
    def boom(_request):
        raise RuntimeError("connection reset")

    monkeypatch.setitem(fetch.ACTIONS, "cart.show", boom)
    code, events = run_main({"action": "cart.show"}, monkeypatch, capsys)

    assert code == 1
    assert "unverified" not in events[-1]


def test_action_registry_contains_nothing_forbidden():
    fetch.assert_action_surface_is_safe()
    for name in fetch.ACTIONS:
        assert safety.violations(name) == []


def test_mutating_actions_are_exactly_the_cart_writes():
    # increment and decrement were registered here and implemented nowhere,
    # while the CLI exposed neither. A declared action is a reachable one --
    # the protocol is invocable directly -- so an undeveloped name on this
    # surface is a way in, not a placeholder.
    assert {"cart.add", "cart.remove"} == fetch.MUTATING_ACTIONS


def test_every_mutating_action_is_implemented():
    for name in fetch.MUTATING_ACTIONS:
        handler = fetch.ACTIONS[name]
        assert handler is not None
        assert "not_implemented" not in getattr(handler, "__qualname__", "")


def test_refuses_to_start_if_a_forbidden_action_is_registered(monkeypatch):
    monkeypatch.setitem(fetch.ACTIONS, "cart.place_order", lambda _request: {})
    with pytest.raises(safety.UnsafeNavigation):
        fetch.assert_action_surface_is_safe()


# A closed browser is not a drifted selector and not an internal bug; it gets
# its own code so the CLI can say what actually happened.
def test_a_closed_browser_has_its_own_error_code(monkeypatch, capsys):
    import login as login_module

    def closed(_request):
        raise login_module.BrowserClosed("browser closed while checking link")

    monkeypatch.setitem(fetch.ACTIONS, "session.status", closed)
    code, events = run_main({"action": "session.status"}, monkeypatch, capsys)

    assert code == 1
    assert events[-1]["code"] == "browser_closed"
    assert events[-1].get("unverified") in (False, None)


def test_a_closed_browser_during_a_mutation_is_unverified(monkeypatch, capsys):
    import login as login_module

    def closed(_request):
        raise login_module.BrowserClosed("browser closed mid-add")

    monkeypatch.setitem(fetch.ACTIONS, "cart.add", closed)
    code, events = run_main({"action": "cart.add"}, monkeypatch, capsys)

    assert code == 3
    assert events[-1]["code"] == "browser_closed"
    assert events[-1]["unverified"] is True


def test_probe_reports_browser_lookup_errors(monkeypatch, capsys):
    monkeypatch.setattr(browser, "playwright_version", lambda: "1.48.0")

    def broken():
        browser.LOOKUP_ERRORS["playwright-chromium"] = "Executable doesn't exist"
        return {}

    monkeypatch.setattr(browser, "find_browsers", broken)

    code, events = run_main({"action": "probe", "options": {}}, monkeypatch, capsys)

    assert code == 0
    assert events[-1]["data"]["browser_errors"]["playwright-chromium"] == "Executable doesn't exist"


# --- search ------------------------------------------------------------------


def test_search_is_registered_as_a_real_action():
    # It fails for a specific, reported reason rather than being a placeholder.
    assert fetch.ACTIONS["search"] is not None
    assert fetch.ACTIONS["search"].__name__ == "action_search"


def test_an_error_that_names_its_code_keeps_it(monkeypatch, capsys):
    # A bot block must reach the user as challenge_detected with advice, not as
    # an internal error with a traceback.
    def blocked(_request):
        raise challenge.Blocked("walmart asked for a human")

    monkeypatch.setitem(fetch.ACTIONS, "search", blocked)

    code, events = run_main({"action": "search", "params": {"query": "milk"}}, monkeypatch, capsys)

    assert code == 1
    assert events[-1]["code"] == challenge.CHALLENGE_CODE


def test_a_login_required_error_survives_the_dispatcher(monkeypatch, capsys):
    def signed_out(_request):
        raise search.NotSignedIn(search.NOT_SIGNED_IN)

    monkeypatch.setitem(fetch.ACTIONS, "search", signed_out)

    _code, events = run_main({"action": "search", "params": {"query": "milk"}}, monkeypatch, capsys)

    assert events[-1]["code"] == "login_required"


def test_a_bad_page_is_reported_before_a_browser_opens(monkeypatch):
    # Validating after launching Chromium spends ten seconds telling the user
    # they made a typo.
    monkeypatch.setattr(browser, "open_session", _explode_on_launch)

    with pytest.raises(ValueError):
        fetch.dispatch({"action": "search", "params": {"query": "milk", "page": "zero"}})


def _explode_on_launch(*_args, **_kwargs):
    raise AssertionError("parameters must be validated before a browser opens")


def test_search_is_not_a_mutating_action():
    assert "search" not in fetch.MUTATING_ACTIONS
