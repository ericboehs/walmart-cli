"""Recon recorder tests. Nothing here contacts Walmart: the session is a double.

scripts/recon.py is the only tool in this repo that touches the live site on
purpose, so the parts that decide *whether* it runs and *what it leaves behind*
are worth testing offline.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import pathlib
import sys
import threading
import time
from pathlib import Path

import pytest

import browser
import safety
from conftest import FakeContext, FakePage

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_recon():
    spec = importlib.util.spec_from_file_location("recon", REPO_ROOT / "scripts" / "recon.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recon = load_recon()


def make_session(page):
    options = browser.BrowserOptions(profile_dir="/tmp/profile", profile_lock="/tmp/profile.lock")
    return browser.Session(FakeContext(page), options)


def test_pump_waits_through_playwright_rather_than_sleeping(monkeypatch):
    # If this ever goes back to time.sleep, the sync API stops dispatching
    # response callbacks and a recon run records almost nothing.
    def forbidden(_seconds):
        raise AssertionError("time.sleep would stop the response handlers")

    monkeypatch.setattr(time, "sleep", forbidden)
    page = FakePage()

    recon.pump(make_session(page), 4.0)

    assert sum(page.waits) == pytest.approx(4000)


def test_pump_slices_long_waits_so_events_keep_flowing():
    page = FakePage()

    recon.pump(make_session(page), 10.0)

    assert len(page.waits) > 1
    assert max(page.waits) <= recon.PUMP_SLICE_SECONDS * 1000


def test_pump_reports_progress_during_a_long_observation(capsys):
    page = FakePage()

    recon.pump(make_session(page), 90.0, countdown=True)

    assert "s left" in capsys.readouterr().out


def test_pump_of_zero_seconds_does_nothing():
    page = FakePage()

    recon.pump(make_session(page), 0)

    assert page.waits == []


def test_a_closed_stdin_is_not_consent(monkeypatch, capsys):
    def eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    plan = recon.recon_plans.PLANS["search"]

    assert recon.confirm_live(plan, Path("/tmp/out"), keep_raw=False) is False
    assert "aborted" in capsys.readouterr().err


def test_a_wrong_answer_is_not_consent(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    plan = recon.recon_plans.PLANS["search"]

    assert recon.confirm_live(plan, Path("/tmp/out"), keep_raw=False) is False


def test_the_typed_word_is_consent(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: " recon ")
    plan = recon.recon_plans.PLANS["search"]

    assert recon.confirm_live(plan, Path("/tmp/out"), keep_raw=False) is True


class RefusingSession:
    """A session whose first navigation trips the safety guard."""

    def __init__(self, page):
        self.page = page

    def on_response(self, handler):
        self.handler = handler

    def goto(self, url, **kwargs):  # noqa: ARG002
        raise safety.UnsafeNavigation(f"refusing to navigate to {url}")


def run_recon(monkeypatch, tmp_path, session, argv):
    @contextlib.contextmanager
    def fake_open_session(_options):
        yield session

    monkeypatch.setattr(recon.browser, "open_session", fake_open_session)
    return recon.main([*argv, "--live", "--yes", "--out", str(tmp_path)])


def test_a_refused_navigation_still_leaves_an_index(monkeypatch, tmp_path):
    # Bodies on disk with no index are undocumented captures of unknown scrub
    # status -- the artifact state invariant 9 is about.
    status = run_recon(monkeypatch, tmp_path, RefusingSession(FakePage()), ["search"])

    index = json.loads((tmp_path / "index.json").read_text())

    assert status == 2
    assert any("aborted early" in note for note in index["notes"])
    assert index["scrubbed"] is True


def test_a_refused_navigation_says_so_on_stderr(monkeypatch, tmp_path, capsys):
    run_recon(monkeypatch, tmp_path, RefusingSession(FakePage()), ["search"])

    assert "refused:" in capsys.readouterr().err


class InterruptedSession:
    def __init__(self, page):
        self.page = page

    def on_response(self, handler):
        self.handler = handler

    def goto(self, url, **kwargs):  # noqa: ARG002
        raise KeyboardInterrupt


def test_ctrl_c_during_a_run_still_leaves_an_index(monkeypatch, tmp_path):
    status = run_recon(monkeypatch, tmp_path, InterruptedSession(FakePage()), ["search"])

    index = json.loads((tmp_path / "index.json").read_text())

    assert status == 2
    assert "aborted early: interrupted" in index["notes"]


class QuietSession:
    """Navigates fine and returns nothing interesting."""

    def __init__(self, page):
        self.page = page
        self.visited = []

    def on_response(self, handler):
        self.handler = handler

    def goto(self, url, **kwargs):  # noqa: ARG002
        self.visited.append(url)
        return None


def test_a_clean_run_writes_an_index_and_a_summary(monkeypatch, tmp_path):
    session = QuietSession(FakePage())
    status = run_recon(monkeypatch, tmp_path, session, ["search", "--settle", "0"])

    assert status == 0
    assert session.visited
    assert (tmp_path / "index.json").exists()
    assert (tmp_path / "summary.md").exists()


def test_settling_between_visits_pumps_the_event_loop(monkeypatch, tmp_path):
    page = FakePage()
    run_recon(monkeypatch, tmp_path, QuietSession(page), ["search", "--settle", "3"])

    assert sum(page.waits) == pytest.approx(3000 * len(recon.recon_plans.PLANS["search"].urls))


def test_recon_refuses_without_the_live_flag(tmp_path, capsys):
    assert recon.main(["search", "--out", str(tmp_path)]) == 2
    assert "refusing to run without --live" in capsys.readouterr().err


def test_a_refusal_never_opens_a_session(monkeypatch, tmp_path):
    def explode(_options):
        raise AssertionError("no session may be opened without --live")

    monkeypatch.setattr(recon.browser, "open_session", explode)

    assert recon.main(["search", "--out", str(tmp_path)]) == 2


def teardown_module():
    sys.modules.pop("recon", None)


# --- Persisted GraphQL ------------------------------------------------------
#
# Walmart uses persisted queries: /orchestra/<app>/graphql/<Operation>/<sha256>,
# usually GET with no body. The first live capture reported zero operations for
# 42 GraphQL calls because only post_data was consulted.


class FakeRequest:
    def __init__(self, url, method="GET", post_data=None):
        self.url = url
        self.method = method
        self.post_data = post_data
        self.resource_type = "xhr"
        self.headers = {}


def recorder(tmp_path, keep_raw=False):
    return recon.Recorder(tmp_path, keep_raw, [])


def test_the_operation_comes_from_the_url_path(tmp_path):
    request = FakeRequest(
        "https://www.walmart.com/orchestra/home/graphql/getCart/fbd4f2dd69076197b55ff3a6cd7d58a9?variables=%7B%7D"
    )

    assert recorder(tmp_path).graphql_operation(request) == "home/getCart"


def test_the_app_prefix_distinguishes_two_operations_of_the_same_name(tmp_path):
    rec = recorder(tmp_path)
    home = FakeRequest("https://www.walmart.com/orchestra/home/graphql/ItemHistory/aaaa")
    cph = FakeRequest("https://www.walmart.com/orchestra/cph/graphql/ItemHistory/aaaa")

    assert rec.graphql_operation(home) == "home/ItemHistory"
    assert rec.graphql_operation(cph) == "cph/ItemHistory"


def test_a_post_body_operation_still_works(tmp_path):
    request = FakeRequest(
        "https://www.walmart.com/swag/graphql",
        method="POST",
        post_data=json.dumps({"operationName": "Search"}),
    )

    assert recorder(tmp_path).graphql_operation(request) == "Search"


def test_a_non_graphql_request_has_no_operation(tmp_path):
    assert recorder(tmp_path).graphql_operation(FakeRequest("https://www.walmart.com/ip/x/1")) is None


# --- Filenames --------------------------------------------------------------


def test_a_filename_never_carries_the_query_string():
    token = "eyJ" + "b" * 7000
    slug = recon.slugify(f"https://www.walmart.com/shoppable-basket?data={token}")

    assert token[:40] not in slug
    assert len(slug) <= 120
    assert "shoppable-basket" in slug


def test_two_queries_on_one_path_get_distinct_filenames():
    a = recon.slugify("https://www.walmart.com/x?q=1")
    b = recon.slugify("https://www.walmart.com/x?q=2")

    assert a != b


def test_a_long_hash_segment_is_digested_not_truncated():
    slug = recon.slugify("https://www.walmart.com/orchestra/home/graphql/getCart/" + "f" * 64)

    assert "getCart" in slug
    assert "f" * 64 not in slug


def test_the_host_and_path_still_describe_the_capture():
    slug = recon.slugify("https://identity.walmart.com/account/login")

    assert "identity.walmart.com" in slug
    assert "login" in slug


# --- Recorded URLs ----------------------------------------------------------


def test_recorded_urls_are_scrubbed(tmp_path):
    rec = recorder(tmp_path)

    assert "REDACTED" in rec.safe_url("https://www.walmart.com/x?email=eric@example.com")


def test_keep_raw_records_the_url_verbatim(tmp_path):
    # --keep-raw is an explicit, warned-about choice; it must not silently scrub.
    rec = recorder(tmp_path, keep_raw=True)
    url = "https://www.walmart.com/x?email=eric@example.com"

    assert rec.safe_url(url) == url


# --- Interpreter and entry point --------------------------------------------
#
# The first live attempt never reached the network. The shebang picked the
# system Python, playwright was not there, and a consented run died with a
# traceback after writing an empty capture directory. Three separate defects,
# each tested below: the wrong interpreter, consent asked before a knowable
# failure, and an environment error dressed up as a crash.


def test_a_missing_playwright_stops_the_run_before_the_consent_prompt(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(recon, "playwright_available", lambda: False)

    def must_not_ask(*_args, **_kwargs):
        raise AssertionError("consent is for network traffic, not for a broken environment")

    monkeypatch.setattr(recon, "confirm_live", must_not_ask)

    status = recon.main(["--live", "observe", "--out", str(tmp_path)])

    assert status == 2
    assert "playwright is not installed" in capsys.readouterr().err


def test_the_preflight_message_names_the_interpreter_and_the_fix(monkeypatch):
    monkeypatch.setattr(recon, "playwright_available", lambda: False)

    message = recon.preflight_interpreter()

    assert sys.executable in message
    assert "uv sync" in message


def test_a_working_interpreter_has_nothing_to_report(monkeypatch):
    monkeypatch.setattr(recon, "playwright_available", lambda: True)

    assert recon.preflight_interpreter() is None


def test_the_script_re_execs_under_the_project_venv(monkeypatch, tmp_path):
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    monkeypatch.setattr(recon, "VENV_PYTHON", venv_python)
    monkeypatch.setattr(recon, "playwright_available", lambda: False)
    monkeypatch.delenv(recon.REEXEC_GUARD, raising=False)
    calls = []

    recon.maybe_reexec(["--live", "search"], execve=lambda *args: calls.append(args))

    assert len(calls) == 1
    path, argv, env = calls[0]
    assert path == str(venv_python)
    assert argv[1:] == [str(recon.SCRIPT_PATH), "--live", "search"]
    assert env[recon.REEXEC_GUARD] == "1"


def test_the_re_exec_guard_prevents_an_infinite_loop(monkeypatch, tmp_path):
    # A venv that is itself missing playwright would otherwise re-exec forever.
    venv_python = tmp_path / "python"
    venv_python.touch()
    monkeypatch.setattr(recon, "VENV_PYTHON", venv_python)
    monkeypatch.setattr(recon, "playwright_available", lambda: False)
    monkeypatch.setenv(recon.REEXEC_GUARD, "1")
    calls = []

    recon.maybe_reexec(["--live", "search"], execve=lambda *args: calls.append(args))

    assert calls == []


def test_a_working_interpreter_is_not_replaced(monkeypatch, tmp_path):
    venv_python = tmp_path / "python"
    venv_python.touch()
    monkeypatch.setattr(recon, "VENV_PYTHON", venv_python)
    monkeypatch.setattr(recon, "playwright_available", lambda: True)
    monkeypatch.delenv(recon.REEXEC_GUARD, raising=False)
    calls = []

    recon.maybe_reexec(["--list"], execve=lambda *args: calls.append(args))

    assert calls == []


def test_no_venv_means_no_re_exec(monkeypatch, tmp_path):
    monkeypatch.setattr(recon, "VENV_PYTHON", tmp_path / "absent" / "python")
    monkeypatch.setattr(recon, "playwright_available", lambda: False)
    monkeypatch.delenv(recon.REEXEC_GUARD, raising=False)
    calls = []

    recon.maybe_reexec(["--live", "search"], execve=lambda *args: calls.append(args))

    assert calls == []


def test_the_running_interpreter_is_never_its_own_re_exec_target(monkeypatch):
    monkeypatch.setattr(recon, "VENV_PYTHON", Path(sys.executable))

    assert recon.project_interpreter() is None


def test_an_unavailable_browser_is_reported_without_a_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(recon, "playwright_available", lambda: True)

    def unavailable(_options):
        raise browser.BrowserUnavailable("no chromium; run: uv run playwright install chromium")

    monkeypatch.setattr(recon.browser, "open_session", unavailable)
    out_dir = tmp_path / "capture"

    status = recon.main(["--live", "--yes", "observe", "--seconds", "0", "--out", str(out_dir)])

    assert status == 2
    assert "no chromium" in capsys.readouterr().err

    # The empty capture must say why it is empty. A directory of nothing with
    # no explanation is exactly what the first failed run left behind.
    index = json.loads((out_dir / "index.json").read_text())
    assert any("browser unavailable" in note for note in index["notes"])
    assert index["entries"] == []


@contextlib.contextmanager
def _session_of(page):
    yield make_session(page)


def test_the_entry_point_reaches_open_session(monkeypatch, tmp_path):
    # Everything above this line is about failing well. This is the path where
    # nothing is wrong: main() must actually get a session and drive the plan.
    monkeypatch.setattr(recon, "playwright_available", lambda: True)
    page = FakePage(url="https://www.walmart.com/")
    opened = []

    def open_session(options):
        opened.append(options)
        return _session_of(page)

    monkeypatch.setattr(recon.browser, "open_session", open_session)
    out_dir = tmp_path / "capture"

    status = recon.main(
        ["--live", "--yes", "store", "--settle", "0", "--out", str(out_dir), "--profile", str(tmp_path / "p")]
    )

    assert status == 0
    assert len(opened) == 1
    assert page.navigations == recon.recon_plans.PLANS["store"].urls

    index = json.loads((out_dir / "index.json").read_text())
    assert index["plan"] == "store"
    assert all("error" not in note for note in index["notes"])


# --- Third-party noise ------------------------------------------------------
#
# 321 of 395 bodies in the search run were ~partytown/proxytown, a web worker
# that proxies other people's scripts. Eighty-one percent of the capture, and
# not one of them an endpoint we could ever call.


class FakeResponse:
    def __init__(self, url, body=b'{"ok":true}', status=200, content_type="application/json"):
        self.url = url
        self.status = status
        self.headers = {"content-type": content_type}
        self._body = body
        self.request = FakeRequest(url)

    def body(self):
        return self._body


def test_partytown_traffic_is_not_recorded(tmp_path):
    rec = recorder(tmp_path)

    rec.on_response(FakeResponse("https://www.walmart.com/~partytown/proxytown"))

    assert rec.entries == []
    assert rec.counter == 0


def test_skipped_traffic_is_counted_rather_than_forgotten(tmp_path):
    # Silence would make the capture lie by omission: a reader would think the
    # site simply never made those calls.
    rec = recorder(tmp_path)

    for _ in range(3):
        rec.on_response(FakeResponse("https://www.walmart.com/~partytown/proxytown"))

    assert rec.skipped == {"/~partytown/": 3}


def test_safety_gets_first_look_at_a_forbidden_noise_url(tmp_path):
    rec = recorder(tmp_path)

    rec.on_response(FakeResponse("https://www.walmart.com/~partytown/checkout"))

    assert rec.skipped == {}
    assert rec.entries == [
        {"url": "https://www.walmart.com/~partytown/checkout", "note": "forbidden url observed"}
    ]


def test_the_index_reports_what_was_skipped(tmp_path):
    rec = recorder(tmp_path)
    rec.on_response(FakeResponse("https://www.walmart.com/~partytown/proxytown"))

    rec.write_index(recon.recon_plans.PLANS["search"], [])

    index = json.loads((tmp_path / "index.json").read_text())
    assert index["skipped_as_noise"] == {"/~partytown/": 1}
    assert "1x `/~partytown/`" in (tmp_path / "summary.md").read_text()


def test_a_capture_with_no_noise_says_nothing_about_it(tmp_path):
    rec = recorder(tmp_path)

    rec.write_index(recon.recon_plans.PLANS["search"], [])

    assert json.loads((tmp_path / "index.json").read_text())["skipped_as_noise"] == {}
    assert "skipped as third-party noise" not in (tmp_path / "summary.md").read_text()


def test_real_walmart_traffic_is_still_recorded(tmp_path):
    rec = recorder(tmp_path)

    rec.on_response(FakeResponse("https://www.walmart.com/orchestra/snb/graphql/Search/abc/search"))

    assert len(rec.entries) == 1
    assert rec.entries[0]["graphql_operation"] == "snb/Search"


def test_bot_protection_traffic_is_kept_because_it_is_evidence(tmp_path):
    # Noise by volume, evidence by nature: this is how we know what gates the
    # login flow. Filtering it would delete the finding.
    rec = recorder(tmp_path)

    rec.on_response(FakeResponse("https://collector-pxu6b0qd2s.px-cloud.net/api/v2/collector"))

    assert len(rec.entries) == 1
    assert rec.skipped == {}


# --- Capture files are private from the instant they exist ---------------------


def test_a_written_capture_file_is_owner_only(tmp_path):
    path = recon.write_private(tmp_path / "body.json", '{"ok": true}')

    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert path.read_text() == '{"ok": true}'


def test_a_capture_file_is_never_world_readable_even_for_an_instant(tmp_path, monkeypatch):
    # The surviving mode cannot see write-then-chmod: the gap is between the
    # two calls. Neutering chmod leaves only the creation mode, and a permissive
    # umask makes the failure loud.
    monkeypatch.setattr(pathlib.Path, "chmod", lambda self, mode: None)
    original = os.umask(0)
    try:
        path = recon.write_private(tmp_path / "body.json", "{}")
    finally:
        os.umask(original)

    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_an_existing_capture_file_is_replaced_not_appended(tmp_path):
    recon.write_private(tmp_path / "body.json", "first attempt, longer")
    path = recon.write_private(tmp_path / "body.json", "second")

    assert path.read_text() == "second"


# --- the capture records by allowlist, not denylist --------------------------------
#
# The denylist in safety.py cannot enumerate an order operation Walmart has not
# shipped yet. It is the alarm; this is the lock. A capture that only keeps what
# it came for cannot be surprised by a name nobody has seen.


def _capture_module():
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "scripts" / "capture-cart-mutation.py"
    spec = importlib.util.spec_from_file_location("capture_cart_mutation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CaptureRequest:
    def __init__(self, url, body=None, method="POST"):
        self.url = url
        self.method = method
        self.post_data = body
        self.resource_type = "fetch"
        self.headers = {}
        self.frame = None


def _recorder(tmp_path):
    return _capture_module().Recorder(tmp_path)


ORCHESTRA = "https://www.walmart.com/orchestra/cartxo/graphql"


ITEMS_BODY = '{"variables":{"input":{"items":[{"quantity":1}]}}}'


def test_the_operation_it_came_for_is_recorded(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.on_request(CaptureRequest(f"{ORCHESTRA}/updateItems/h", ITEMS_BODY))
    assert recorder.mutations == 1


def test_an_unasked_for_operation_is_not_recorded(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.on_request(CaptureRequest(f"{ORCHESTRA}/SomeFutureMutation/h", '{"a":1}'))
    assert recorder.mutations == 0
    assert recorder.skipped == 1
    assert recorder.refused == 0


def test_an_order_flow_operation_is_refused_and_counted_apart(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.on_request(CaptureRequest(f"{ORCHESTRA}/reserveSlotMutation/h", '{"a":1}'))
    assert recorder.mutations == 0
    assert recorder.refused == 1


def test_a_body_naming_a_different_operation_is_refused(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.on_request(CaptureRequest(f"{ORCHESTRA}/updateItems/h", '{"operationName":"CreateContract"}'))
    assert recorder.mutations == 0
    assert recorder.refused == 1


def test_nothing_forbidden_reaches_the_written_document(tmp_path):
    recorder = _recorder(tmp_path)
    for name in ("updateItems", "reserveSlotMutation", "CreateContract", "updateTipping"):
        recorder.on_request(CaptureRequest(f"{ORCHESTRA}/{name}/h", ITEMS_BODY))
    recorder.write([])
    written = (tmp_path / "capture.json").read_text()
    for name in ("reserveSlot", "CreateContract", "updateTipping"):
        assert name not in written, name


# --- the body must be the shape we came for, not merely not-disagree ----------------
#
# Rejecting only bodies that disagree with the URL lets an unclassified body in,
# because a body naming nothing disagrees with nothing. None of the four
# observed updateItems requests carries operationName -- persisted queries put
# the operation in the path -- so the discriminator has to be structural.


def test_the_observed_body_shape_is_recorded(tmp_path):
    recorder = _recorder(tmp_path)
    body = '{"variables":{"input":{"items":[{"quantity":1}]}}}'
    recorder.on_request(CaptureRequest(f"{ORCHESTRA}/updateItems/h", body))
    assert recorder.mutations == 1
    # The record names the operation the guard cleared, not the persisted hash
    # that follows it in the path. The first real capture labelled all four
    # requests with the hash.
    assert recorder.events[0]["operation"] == "updateItems"


def test_the_recorded_operation_is_the_one_the_guard_allowed(tmp_path):
    """The document and the guard must not answer from different code."""
    recorder = _recorder(tmp_path)
    body = '{"variables":{"input":{"items":[{"quantity":1}]}}}'
    hashed = f"{ORCHESTRA}/updateItems/45fca98d0f0fb2ffc2ccf8a3290b8914"
    recorder.on_request(CaptureRequest(hashed, body))
    recorder.write([])
    written = json.loads((tmp_path / "capture.json").read_text())
    recorded = {entry["operation"] for entry in written["mutating_requests"]}
    assert recorded == {"updateItems"}
    assert recorded <= set(_capture_module().ALLOWED_OPERATIONS)


def test_no_observed_request_carries_an_operation_name():
    """Pins the evidence the structural discriminator rests on."""
    capture = Path(__file__).resolve().parent / "recon"
    files = sorted(capture.glob("cart-mutation-*/capture.json"))
    if not files:
        pytest.skip("no live capture on this machine")
    checked = 0
    for file in files:
        for entry in json.loads(file.read_text())["mutating_requests"]:
            # Captures written before the shape-only rewrite carry "body";
            # everything since carries "body_shape" and no body at all. The
            # claim is the same either way, and a test pinned to one spelling
            # of the evidence stops checking the evidence the day the format
            # changes -- which is what happened here.
            recorded = entry.get("body", entry.get("body_shape"))
            assert recorded is not None, "a request record with neither body nor shape"
            assert "operationName" not in json.dumps(recorded)
            checked += 1
    assert checked, "capture files present but no mutating requests in them"


@pytest.mark.parametrize(
    "body",
    [
        "{}",
        '{"variables":{}}',
        '{"variables":{"input":{}}}',
        '{"variables":{"input":{"items":"not a list"}}}',
        '{"variables":"text"}',
        "[]",
        "not json",
        None,
    ],
)
def test_an_unrecognised_body_does_not_enter_the_evidence_file(tmp_path, body):
    recorder = _recorder(tmp_path)
    recorder.on_request(CaptureRequest(f"{ORCHESTRA}/updateItems/h", body))
    assert recorder.mutations == 0
    recorder.write([])
    assert json.loads((tmp_path / "capture.json").read_text())["mutating_requests"] == []


# --- the response side ---------------------------------------------------------------
#
# The remaining unknown is what a successful updateItems looks like, so the
# capture now records answers as well as questions. The rules are tighter on
# this side: a response is read only if it answers a request that already
# passed every check, and an order-flow body is never fetched at all.


class CaptureResponse:
    """A response that records whether anyone asked for its body."""

    def __init__(self, url, request=None, status=200, text='{"data":{"updateItems":{}}}'):
        self.url = url
        self.request = request
        self.status = status
        self.ok = 200 <= status < 300
        self.read = 0
        self._text = text

    def text(self):
        self.read += 1
        if self._text is None:
            raise RuntimeError("no body")
        return self._text


def _cleared_request(recorder, url=None):
    """A request the recorder has already accepted, as the response hook requires."""
    request = CaptureRequest(url or f"{ORCHESTRA}/updateItems/h", ITEMS_BODY)
    recorder.on_request(request)
    assert recorder.mutations == 1
    return request


def _forcibly_cleared(recorder, url):
    """Mark a request as cleared without going through on_request.

    Only for isolating one guard at a time. The first version of the
    order-flow, allowlist and same-host response tests used an uncleared
    request, so the correlation check refused them before the guard under test
    was reached: three tests passing for a reason unrelated to their names, and
    three mutants surviving because of it. A guard has to be the only thing
    that can stop the request it is asked about.
    """
    request = CaptureRequest(url, ITEMS_BODY)
    recorder._cleared[id(request)] = request
    return request


def test_the_answer_to_a_cleared_request_is_recorded(tmp_path):
    recorder = _recorder(tmp_path)
    request = _cleared_request(recorder)
    recorder.on_response(CaptureResponse(request.url, request))
    assert len(recorder.responses) == 1
    assert recorder.responses[0]["status"] == 200
    assert recorder.responses[0]["operation"] == "updateItems"


def test_an_order_flow_response_body_is_never_even_fetched(tmp_path):
    """Not read and discarded -- not read. Reading it puts it in this process."""
    recorder = _recorder(tmp_path)
    for name in ("reserveSlotMutation", "CreateContract", "placeOrderMutation", "updateTipping"):
        url = f"{ORCHESTRA}/{name}/h"
        # Cleared on purpose, so refusing is the order-flow guard's doing and
        # not the correlation check's.
        response = CaptureResponse(url, _forcibly_cleared(recorder, url))
        recorder.on_response(response)
        assert response.read == 0, name
    assert recorder.responses == []
    # Removing the order-flow check leaves this at zero and the bodies still
    # unread, because the allowlist stops them anyway. That is the arrangement
    # working as intended -- the allowlist is the guarantee and the denylist is
    # the alarm -- but an alarm that does not sound is not installed, and the
    # count is the only place it sounds.
    assert recorder.refused == 4


def test_an_unasked_for_operations_response_is_not_read(tmp_path):
    recorder = _recorder(tmp_path)
    url = f"{ORCHESTRA}/SomeFutureMutation/h"
    response = CaptureResponse(url, _forcibly_cleared(recorder, url))
    recorder.on_response(response)
    assert response.read == 0
    assert recorder.responses == []


def test_a_response_to_a_request_we_never_cleared_is_counted_not_read(tmp_path):
    """Correlation says which request this answers; it does not say it was allowed."""
    recorder = _recorder(tmp_path)
    response = CaptureResponse(f"{ORCHESTRA}/updateItems/h", CaptureRequest("elsewhere"))
    recorder.on_response(response)
    assert response.read == 0
    assert recorder.responses == []
    assert recorder.uncorrelated_responses == 1


def test_a_response_with_no_request_at_all_is_not_read(tmp_path):
    recorder = _recorder(tmp_path)
    response = CaptureResponse(f"{ORCHESTRA}/updateItems/h", None)
    recorder.on_response(response)
    assert response.read == 0
    assert recorder.uncorrelated_responses == 1


def test_a_non_walmart_response_is_ignored(tmp_path):
    recorder = _recorder(tmp_path)
    url = "https://example.com/orchestra/cartxo/graphql/updateItems/h"
    response = CaptureResponse(url, _forcibly_cleared(recorder, url))
    recorder.on_response(response)
    assert response.read == 0
    assert recorder.responses == []


def test_a_body_that_cannot_be_read_is_a_finding_not_a_crash(tmp_path):
    recorder = _recorder(tmp_path)
    request = _cleared_request(recorder)
    recorder.on_response(CaptureResponse(request.url, request, text=None))
    assert recorder.unreadable_responses == 1
    # Was "null" until the first live run showed what that hides: a body that
    # raised on read, recorded as though the server had answered null.
    assert recorder.responses[0]["body_shape"] == _capture_module().UNREADABLE
    assert recorder.responses[0]["status"] == 200


def test_a_rejection_is_recorded_as_readily_as_a_success(tmp_path):
    """The failing shape is contract evidence too, and likelier to arrive first."""
    recorder = _recorder(tmp_path)
    request = _cleared_request(recorder)
    recorder.on_response(
        CaptureResponse(request.url, request, status=400, text='{"errors":[{"message":"no"}]}')
    )
    assert recorder.responses[0]["status"] == 400
    assert recorder.responses[0]["ok"] is False


def test_the_document_carries_the_responses(tmp_path):
    recorder = _recorder(tmp_path)
    request = _cleared_request(recorder)
    recorder.on_response(CaptureResponse(request.url, request))
    recorder.write([])
    written = json.loads((tmp_path / "capture.json").read_text())
    assert len(written["responses"]) == 1
    assert written["responses_not_read_because_uncorrelated"] == 0


# --- the shape, which is the part that can eventually be quoted -----------------------


def test_the_shape_keeps_the_structure_and_none_of_the_values(tmp_path):
    shape_of = _capture_module().shape_of
    shape = shape_of({"data": {"updateItems": {"name": "SECRET", "quantity": 12.5}}})
    assert shape == {"data": {"updateItems": {"name": "str", "quantity": "float"}}}


def test_the_shape_names_booleans_apart_from_numbers():
    """bool is an int in Python, and that has already caused two defects here."""
    shape_of = _capture_module().shape_of
    assert shape_of({"data": {"isQSRItem": True}}) == {"data": {"isQSRItem": "boolean"}}
    assert shape_of({"data": {"isQSRItem": 1}}) == {"data": {"isQSRItem": "int"}}


def test_an_empty_list_is_a_different_observation_from_a_full_one():
    shape_of = _capture_module().shape_of
    assert shape_of({"errors": []}) == {"errors": {"list": 0}}
    assert shape_of({"errors": [{"message": "x"}]}) == {"errors": {"list": 1, "of": {"message": "str"}}}


def test_the_shape_walk_is_bounded():
    """An unbounded walk over a document we have never seen is a way to hang."""
    shape_of = _capture_module().shape_of
    module = _capture_module()
    deep: dict = {}
    node = deep
    for _ in range(module.MAX_SHAPE_DEPTH + 20):
        node["next"] = {}
        node = node["next"]
    assert "..." in json.dumps(shape_of(deep))


def test_the_shape_of_a_recorded_response_contains_no_recorded_value(tmp_path):
    """The shape is the promotable artefact, so it must carry nothing."""
    recorder = _recorder(tmp_path)
    request = _cleared_request(recorder)
    recorder.on_response(
        CaptureResponse(request.url, request, text='{"data":{"cart":{"id":"CART-SECRET-1"}}}')
    )
    assert "CART-SECRET-1" not in json.dumps(recorder.responses[0]["body_shape"])


# --- the response body is not written down -------------------------------------------
#
# The request side is scrubbed and kept. The response side is not kept at all
# on the first run: no updateItems response has ever been seen, it may carry a
# whole cart or the household behind it, and a scrubber works from a list of
# field names that cannot be shown to recognise fields nobody has looked at.
# Structure, then, and none of the data.


def test_no_value_from_a_response_reaches_the_file(tmp_path):
    """Not scrubbed and kept -- not kept. A denylist cannot vouch for the unseen."""
    recorder = _recorder(tmp_path)
    request = _cleared_request(recorder)
    body = json.dumps(
        {
            "data": {
                "cartId": "cart-id-canary-0000-not-a-real-cart",
                "customer": {"email": "someone@example.test", "firstName": "Canary"},
                "address": "123 Nowhere Road",
                "items": [{"name": "A Product Nobody Should See", "price": 12.34}],
            }
        }
    )
    recorder.on_response(CaptureResponse(request.url, request, text=body))
    recorder.write([])
    written = (tmp_path / "capture.json").read_text()
    for secret in (
        "cart-id-canary-0000-not-a-real-cart",
        "someone@example.test",
        "Canary",
        "123 Nowhere Road",
        "A Product Nobody Should See",
        "12.34",
    ):
        assert secret not in written, secret


def test_the_record_has_no_body_field_at_all(tmp_path):
    """A field that is not there cannot be filled in later by accident."""
    recorder = _recorder(tmp_path)
    request = _cleared_request(recorder)
    recorder.on_response(CaptureResponse(request.url, request))
    assert "body" not in recorder.responses[0]


def test_the_structure_survives_even_though_the_values_do_not(tmp_path):
    """Dropping the body must not drop the contract, which is the point of the run."""
    recorder = _recorder(tmp_path)
    request = _cleared_request(recorder)
    recorder.on_response(CaptureResponse(request.url, request, text='{"data":{"updateItems":{"name":"x"}}}'))
    assert recorder.responses[0]["body_shape"] == {"data": {"updateItems": {"name": "str"}}}


# --- what the run amounted to ---------------------------------------------------------


def test_a_session_that_never_opened_is_not_a_finding_about_walmart():
    verdict = _capture_module().verdict
    assert "never opened" in verdict(watched=False, mutations=0, responses=0)


def test_no_request_at_all_says_so():
    verdict = _capture_module().verdict
    assert "No mutating request" in verdict(watched=True, mutations=0, responses=0)


def test_requests_without_responses_is_a_failure_not_a_quiet_success():
    """The response is the point of the run; getting only requests is not partial credit."""
    verdict = _capture_module().verdict
    problem = verdict(watched=True, mutations=3, responses=0)
    assert problem is not None
    assert "no response" in problem


def test_getting_what_it_came_for_says_nothing():
    verdict = _capture_module().verdict
    assert verdict(watched=True, mutations=1, responses=1) is None


# --- keys are the part of a shape that is copied verbatim -----------------------------
#
# "Carries the contract and none of the contents" was false while every key was
# kept. A response with a map keyed by email, order id or cart id puts the
# secret in the key position, where nothing was looking.


def test_a_key_that_looks_perfectly_like_a_field_name_is_still_collapsed():
    """The hole the pattern left: a first name is identifier-shaped and short.

    `Eric`, `ashley` and a short opaque token all satisfy "looks like a GraphQL
    field", which is why the gate is a list of observed field names rather than
    a description of what one looks like. The earlier canaries used an email
    and a UUID, both of which fail a pattern for reasons unrelated to the claim.
    """
    module = _capture_module()
    shaped = module.shape_of(
        {
            "data": {
                "Eric": {"quantity": 1},
                "ashley": {"quantity": 2},
                "a7Kd93Lm2Qr8Xz01BvNt": {"quantity": 3},
            }
        }
    )
    rendered = json.dumps(shaped)
    for identifier in ("Eric", "ashley", "a7Kd93Lm2Qr8Xz01BvNt"):
        assert identifier not in rendered, identifier
    assert shaped["data"][module.DYNAMIC_KEYS]["count"] == 3


def test_a_secret_used_as_an_object_key_does_not_survive(tmp_path):
    shape_of = _capture_module().shape_of
    shaped = shape_of(
        {
            "data": {
                "someone@example.test": {"quantity": 1},
                "cart-id-canary-0000-not-a-real-cart": {"quantity": 2},
            }
        }
    )
    rendered = json.dumps(shaped)
    assert "someone@example.test" not in rendered
    assert "cart-id-canary-0000-not-a-real-cart" not in rendered


def test_dynamic_keys_are_counted_without_being_quoted():
    """A map keyed by id is itself a shape, so the count is worth keeping."""
    module = _capture_module()
    shaped = module.shape_of({"data": {"a@b.test": {"name": 1}, "c@d.test": {"name": 2}}})
    dynamic = shaped["data"][module.DYNAMIC_KEYS]
    assert dynamic["count"] == 2
    assert dynamic["of"] == {"name": "int"}


def test_known_keys_are_kept_beside_unknown_ones():
    module = _capture_module()
    shaped = module.shape_of({"updateItems": {"quantity": 1}, "9f8e@x.test": {"quantity": 2}})
    assert shaped["updateItems"] == {"quantity": "int"}
    assert module.DYNAMIC_KEYS in shaped


def test_the_allowlist_holds_field_names_and_nothing_that_could_be_a_person():
    """Provenance check: every entry is an envelope key or an observed field."""
    module = _capture_module()
    assert module.is_known_key("data")
    assert module.is_known_key("updateItems")
    assert module.is_known_key("quantity")
    for not_a_field in (
        "Eric",
        "ashley",
        "household1",
        "a7Kd93Lm2Qr8Xz01BvNt",
        "someone@example.test",
        "45fca98d0f0fb2ffc2ccf8a3290b891445fca98d0f0fb2ffc2ccf8a3290b8914",
        "6f1b2c3d-4e5f-6789-abcd-ef0123456789",
        12,
    ):
        assert not module.is_known_key(not_a_field), not_a_field


def test_an_unknown_key_count_is_what_tells_us_to_look_again():
    """A high count is not a failed capture; it is the capture asking for a second one."""
    module = _capture_module()
    shaped = module.shape_of({"data": {f"unknown{i}": i for i in range(7)}})
    assert shaped["data"][module.DYNAMIC_KEYS]["count"] == 7


# --- breadth is bounded too -----------------------------------------------------------
#
# The comment said depth, breadth and length were capped. Only depth and length
# were: every dict entry was walked and stored, so a huge object could still
# grow the file or hang the walk.


def test_a_wide_object_of_known_keys_is_capped_and_says_so():
    module = _capture_module()
    known = sorted(module.ALLOWED_SHAPE_KEYS)
    wide = {key: 1 for key in known}
    original = module.MAX_DICT_ITEMS
    try:
        module.MAX_DICT_ITEMS = 3
        shaped = module.shape_of(wide)
    finally:
        module.MAX_DICT_ITEMS = original
    assert len([k for k in shaped if not k.startswith("<")]) == 3
    assert shaped[module.TRUNCATED] == {"total": len(known)}


def test_a_wide_object_of_unknown_keys_is_capped_without_quoting_any():
    module = _capture_module()
    wide = {f"person{i}": {"quantity": i} for i in range(module.MAX_DICT_ITEMS + 20)}
    shaped = module.shape_of(wide)
    rendered = json.dumps(shaped)
    assert "person" not in rendered
    # The count is what was looked at; the total is how many there were. Both
    # are needed: one bounds the work, the other says the work was bounded.
    assert shaped[module.DYNAMIC_KEYS]["count"] == module.MAX_DICT_ITEMS
    assert shaped[module.TRUNCATED]["total"] == module.MAX_DICT_ITEMS + 20


class CountedPairs(dict):
    """A dict that reports how many of its entries were actually consumed.

    items() is a generator so that stopping early is visible. The previous
    version of this test counted recursive calls on the children, which the
    unbounded root walk never touched -- so it passed while every one of a
    million entries was still being visited. Count what the bound is supposed
    to bound.
    """

    def __init__(self, pairs):
        super().__init__(pairs)
        self.yielded = 0

    def items(self):
        for pair in super().items():
            self.yielded += 1
            yield pair


@pytest.mark.parametrize("kind", ["known", "unknown", "mixed"])
def test_no_more_entries_are_consumed_than_the_budget_allows(kind):
    """One budget for the object, spent before anything is looked at."""
    module = _capture_module()
    known = sorted(module.ALLOWED_SHAPE_KEYS)
    # A budget smaller than the allowlist, so the all-known case genuinely
    # exceeds it. With the real budget it does not, and the assertion would
    # hold whether or not anything was bounded.
    budget = 5
    size = budget * 20

    if kind == "known":
        wide = CountedPairs({key: 1 for key in known})
    elif kind == "unknown":
        wide = CountedPairs({f"person{i}": i for i in range(size)})
    else:
        mixed = {f"person{i}": i for i in range(size)}
        mixed.update({key: 1 for key in known})
        wide = CountedPairs(mixed)

    assert len(wide) > budget, f"{kind}: the input does not reach the budget"

    original = module.MAX_DICT_ITEMS
    try:
        module.MAX_DICT_ITEMS = budget
        module.shape_of(wide)
    finally:
        module.MAX_DICT_ITEMS = original

    assert wide.yielded <= budget, f"{kind}: visited {wide.yielded} entries on a budget of {budget}"


def test_known_and_unknown_keys_share_one_budget():
    """Two budgets meant the stated cap allowed twice as many shaped children."""
    module = _capture_module()
    known = sorted(module.ALLOWED_SHAPE_KEYS)
    mixed = {key: 1 for key in known}
    mixed.update({f"person{i}": i for i in range(module.MAX_DICT_ITEMS * 2)})

    shaped = module.shape_of(mixed)
    described = len([key for key in shaped if not key.startswith("<")])
    counted = shaped.get(module.DYNAMIC_KEYS, {}).get("count", 0)
    assert described + counted <= module.MAX_DICT_ITEMS


def test_the_markers_cannot_collide_with_a_real_field_name():
    module = _capture_module()
    for marker in (module.DYNAMIC_KEYS, module.TRUNCATED):
        assert not module.is_known_key(marker)
        assert marker.startswith("<")


# --- a list is not its first element --------------------------------------------------
#
# Element zero is not the contract: the line we care about may not be first,
# and an errors array is heterogeneous by nature.


def test_a_later_element_with_a_different_structure_is_recorded():
    shape_of = _capture_module().shape_of
    shaped = shape_of([{"quantity": 1}, {"name": "x", "isQSRItem": True}])
    assert shaped["list"] == 2
    variants = shaped["variants"]
    assert {"quantity": "int"} in variants
    assert {"name": "str", "isQSRItem": "boolean"} in variants


def test_a_uniform_list_is_described_once():
    shape_of = _capture_module().shape_of
    uniform = [{"quantity": 1}, {"quantity": 2}, {"quantity": 3}]
    assert shape_of(uniform) == {"list": 3, "of": {"quantity": "int"}}


def test_a_long_list_is_bounded_and_says_so():
    module = _capture_module()
    shaped = module.shape_of([{"quantity": i} for i in range(module.MAX_LIST_ITEMS + 10)])
    assert shaped["list"] == module.MAX_LIST_ITEMS + 10
    assert shaped["truncated"] is True


def test_many_different_structures_are_capped_and_say_so():
    module = _capture_module()
    # Distinct structures built from allowlisted keys, so the variety is real
    # rather than every element collapsing to the same dynamic-key shape.
    fields = sorted(module.ALLOWED_SHAPE_KEYS)[: module.MAX_VARIANTS + 3]
    shaped = module.shape_of([{field: 1} for field in fields])
    assert len(shaped["variants"]) == module.MAX_VARIANTS
    assert shaped["truncated"] is True


def test_the_error_shape_a_rejection_would_carry_survives_the_walk():
    """The shape most likely to arrive first, and the one worth reading."""
    shape_of = _capture_module().shape_of
    shaped = shape_of({"errors": [{"message": "x", "path": ["updateItems"]}], "data": None})  # noqa: E501
    assert shaped["data"] == "null"
    assert shaped["errors"]["of"]["message"] == "str"


# --- what the driver is told ----------------------------------------------------------
#
# The prompt said "anything the safety guard forbids aborts it", which was not
# true: a forbidden operation is counted, its body is left unread, and the run
# continues. Telling someone the tool will stop them is worse than telling them
# nothing, because they will rely on it.


def _prompt_text(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    _capture_module().confirm(tmp_path)
    return capsys.readouterr().out


def test_the_prompt_does_not_promise_to_abort(monkeypatch, capsys, tmp_path):
    text = _prompt_text(monkeypatch, capsys, tmp_path).lower()
    assert "abort" not in text
    assert "does not stop" in text


def test_the_prompt_says_the_response_body_is_not_kept(monkeypatch, capsys, tmp_path):
    text = _prompt_text(monkeypatch, capsys, tmp_path)
    assert "SHAPE of the" in text
    assert "Neither body is written down" in text
    assert "no headers are kept" in text


def test_the_prompt_asks_for_one_change_to_an_existing_line(monkeypatch, capsys, tmp_path):
    """One observation is the goal; a broad script invites several mutations."""
    text = _prompt_text(monkeypatch, capsys, tmp_path).lower()
    assert "existing cart" in text
    assert "one line you already have" in text
    assert "add to cart" not in text


def test_the_running_summary_does_not_print_a_dynamic_key(tmp_path, capsys):
    """The terminal and its scrollback are disclosure too."""
    recorder = _recorder(tmp_path)
    request = _cleared_request(recorder)
    capsys.readouterr()
    recorder.on_response(
        CaptureResponse(request.url, request, text='{"data":{"updateItems":{},"someone@example.test":{}}}')
    )
    printed = capsys.readouterr().out
    assert "updateItems" in printed
    assert "someone@example.test" not in printed


def test_an_empty_list_is_not_described_as_having_an_element(tmp_path):
    """{"list": 0} and a first-element description are different observations."""
    shape_of = _capture_module().shape_of
    empty = shape_of({"errors": []})["errors"]
    assert empty == {"list": 0}
    assert "of" not in empty
    assert "variants" not in empty


def test_the_prompt_describes_the_gate_as_recognition_not_syntax(monkeypatch, capsys, tmp_path):
    """The gate is a list of observed names; calling it schema syntax misdescribes it."""
    text = _prompt_text(monkeypatch, capsys, tmp_path)
    assert "unrecognised key" in text
    assert "non-schema" not in text


# --- the request side, minimised for this run -----------------------------------------
#
# The request contract was captured before and has not changed. Keeping the
# body again would add the product chosen, its identifiers and its quantity,
# and would add no evidence. Headers go too: the drop list covers the ones we
# know about, and an unknown one is exactly what a drop list cannot cover.


REVEALING_REQUEST = json.dumps(
    {
        "variables": {
            "input": {
                "cartId": "cart-id-canary-0000-not-a-real-cart",
                "items": [
                    {
                        "offerId": "OFFER-CANARY-77",
                        "usItemId": "999888777",
                        "name": "A Product Nobody Should See",
                        "quantity": 4,
                    }
                ],
            }
        }
    }
)


def _revealing(url=None):
    request = CaptureRequest(url or f"{ORCHESTRA}/updateItems/h", REVEALING_REQUEST)
    request.headers = {
        "authorization": "Bearer canary-token",
        "x-unknown-tracer": "TRACER-CANARY-42",
    }
    return request


def test_no_request_value_or_header_reaches_the_file(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.on_request(_revealing())
    recorder.write([])
    written = (tmp_path / "capture.json").read_text()
    for secret in (
        "cart-id-canary-0000-not-a-real-cart",
        "OFFER-CANARY-77",
        "999888777",
        "A Product Nobody Should See",
        "Bearer canary-token",
        "TRACER-CANARY-42",
        "x-unknown-tracer",
    ):
        assert secret not in written, secret


def test_the_request_record_keeps_metadata_and_shape(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.on_request(_revealing())
    record = recorder.events[0]
    assert "headers" not in record
    assert "body" not in record
    assert record["operation"] == "updateItems"
    assert record["method"] == "POST"
    assert record["from_worker"] is True
    # The structure that made it recognisable survives; the values do not.
    assert record["body_shape"]["variables"]["input"]["items"]["of"]["quantity"] == "int"


def test_the_body_is_still_checked_before_it_is_discarded(tmp_path):
    """Minimising what is kept must not weaken what is verified."""
    recorder = _recorder(tmp_path)
    recorder.on_request(CaptureRequest(f"{ORCHESTRA}/updateItems/h", '{"variables":{"x":1}}'))
    assert recorder.mutations == 0
    assert recorder.unclassified == 1


def test_a_correlated_request_is_released_after_it_is_answered(tmp_path):
    """Nothing later in the session needs it, and a response arrives once."""
    recorder = _recorder(tmp_path)
    request = _revealing()
    recorder.on_request(request)
    assert len(recorder._cleared) == 1
    recorder.on_response(CaptureResponse(request.url, request))
    assert recorder._cleared == {}
    # A repeat of the same response is no longer correlated, so it is not read.
    repeat = CaptureResponse(request.url, request)
    recorder.on_response(repeat)
    assert repeat.read == 0
    assert len(recorder.responses) == 1


# --- a body that will not parse is named, not copied ----------------------------------


def test_an_unparseable_response_is_a_sentinel_not_its_text(tmp_path):
    module = _capture_module()
    recorder = _recorder(tmp_path)
    request = _cleared_request(recorder)
    recorder.on_response(CaptureResponse(request.url, request, text="<html>SECRET-CANARY-IN-HTML</html>"))
    recorder.write([])
    written = (tmp_path / "capture.json").read_text()
    assert "SECRET-CANARY-IN-HTML" not in written
    assert recorder.responses[0]["body_shape"] == module.UNPARSEABLE


def test_an_unparseable_request_is_refused_and_leaves_nothing(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.on_request(CaptureRequest(f"{ORCHESTRA}/updateItems/h", "SECRET-CANARY-NOT-JSON"))
    recorder.write([])
    assert recorder.mutations == 0
    assert "SECRET-CANARY-NOT-JSON" not in (tmp_path / "capture.json").read_text()


def test_the_sentinel_is_not_described_as_a_string():
    """shape_of would call it "str", which is true of the sentinel and says nothing."""
    module = _capture_module()
    assert module._shape(module.UNPARSEABLE) == module.UNPARSEABLE
    assert module.shape_of("some text") == "str"


def test_the_capture_offers_no_flag_it_no_longer_honours(tmp_path):
    """--scrub-literal removed with the last text path; a dead flag is a false promise."""
    module = _capture_module()
    with pytest.raises(SystemExit):
        module.main(["--scrub-literal", "anything", "--yes", "--out", str(tmp_path)])


# --- the wait is not a pause: a blocked thread is a stopped recorder ------------------
#
# The first live capture recorded a 200 whose body it never read, and a note
# saying it had recorded nothing at all. Both come from the same cause: the run
# waited for Enter with a bare input(), and Playwright's sync API dispatches
# response handlers only while this thread is inside a Playwright call. A
# handler that calls response.text() suspends there until teardown, and by then
# the body is gone.
#
# scripts/recon.py has had `pump` for this since its own version of the bug.
# The capture script was written afterwards and blocked anyway, which is why
# these tests live next to that one now.


class _PumpingPage:
    """A page that records how long it was asked to wait."""

    def __init__(self):
        self.waits = []

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


def test_waiting_for_enter_stays_inside_playwright():
    capture = _capture_module()
    page = _PumpingPage()
    # Returns immediately, as a closed stdin would.
    capture.wait_for_enter(page, read_line=lambda: "")

    # It may finish before the first slice, but it must never sleep: every unit
    # of waiting has to be spent somewhere Playwright can dispatch a callback.
    assert all(ms == capture.PUMP_SLICE_SECONDS * 1000 for ms in page.waits)


def test_waiting_for_enter_keeps_pumping_until_the_human_answers():
    capture = _capture_module()
    page = _PumpingPage()
    gate = threading.Event()
    # A bound, because the first version of this test spun until the pump had
    # run and had no way out if it never did. Removing the pump did not fail
    # it -- it hung the suite, and a mutation run sat there for eighty-seven
    # minutes before anyone noticed. A test that cannot fail cannot report.
    deadline = time.monotonic() + 5.0

    def slow_line():
        # Answer only once the page has been pumped a few times, the way a
        # human takes seconds to click. If the wait were a sleep, these slices
        # would not happen and the recorder would be deaf for all of them.
        while len(page.waits) < 3 and time.monotonic() < deadline:
            time.sleep(0.001)
        gate.set()
        return "\n"

    capture.wait_for_enter(page, read_line=slow_line)

    assert gate.is_set()
    assert len(page.waits) >= 3, "the wait never pumped: handlers would not run"


def test_waiting_for_enter_survives_a_closed_stdin():
    capture = _capture_module()

    def closed():
        raise EOFError

    capture.wait_for_enter(_PumpingPage(), read_line=closed)


# --- a body we never read is not a body that was null ---------------------------------


def test_an_unreadable_body_is_not_recorded_as_a_shape():
    """The first run wrote "body_shape": "null" for a body that raised on read.

    Nothing in that file separated it from a server that genuinely answered
    null, so the record asserted a shape nobody had seen.
    """
    capture = _capture_module()
    assert capture.UNREADABLE != "null"
    assert capture._shape(capture.UNREADABLE) == capture.UNREADABLE
    assert capture._shape(None) != capture.UNREADABLE


def test_an_unreadable_body_is_not_an_unparseable_one():
    capture = _capture_module()
    assert capture.UNREADABLE != capture.UNPARSEABLE
    summarise = capture.Recorder._summarise
    assert summarise(capture.UNREADABLE) == "body could not be read"
    assert summarise(capture.UNPARSEABLE) == "body did not parse"


def test_a_response_that_could_not_be_read_is_not_a_success():
    """One request, one 200, and no readable body returned None the first time."""
    verdict = _capture_module().verdict
    problem = verdict(watched=True, mutations=1, responses=1, readable=0)
    assert problem is not None
    assert "not" in problem and "shape" in problem


def test_a_readable_response_is_what_the_run_came_for():
    verdict = _capture_module().verdict
    assert verdict(watched=True, mutations=1, responses=1, readable=1) is None


# --- the note is composed after the counting, not before ------------------------------
#
# The first live capture wrote "recorded 0 mutating requests and 0 responses"
# into a file that contained one of each. The note was built inside the `with`,
# at the moment the human pressed Enter, and the handlers had not finished.
#
# This fake fires its handlers during teardown, which is exactly when the real
# ones ran, so it fails against the old ordering and passes against the new.


class _TeardownSession:
    """A session whose traffic arrives as the context closes."""

    def __init__(self, request, response):
        self.page = _PumpingPage()
        self._request, self._response = request, response
        self._handlers = {}

    def on_worker(self, handler):
        self._handlers["worker"] = handler

    def on_request(self, handler):
        self._handlers["request"] = handler

    def on_response(self, handler):
        self._handlers["response"] = handler

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._handlers["request"](self._request)
        self._handlers["response"](self._response)
        return False


def test_the_note_counts_what_was_recorded_not_what_had_arrived_by_enter(tmp_path, monkeypatch):
    capture = _capture_module()
    body = '{"variables":{"input":{"items":[{"quantity":1}]}}}'
    url = f"{ORCHESTRA}/updateItems/45fca98d0f0fb2ffc2ccf8a3290b8914"
    request = CaptureRequest(url, body)
    session = _TeardownSession(request, CaptureResponse(url, request, text='{"data":{}}'))

    monkeypatch.setattr(capture.browser, "BrowserOptions", lambda **kwargs: object())
    monkeypatch.setattr(capture.browser, "open_session", lambda _options: session)
    monkeypatch.setattr(capture, "wait_for_enter", lambda *a, **k: None)

    args = argparse.Namespace(
        out=str(tmp_path), profile=str(tmp_path / "p"), cdp_url="http://127.0.0.1:9222", yes=True
    )
    capture.run(args)

    written = json.loads((tmp_path / "capture.json").read_text())
    assert written["mutating_requests"], "the fake's request never reached the recorder"
    assert "recorded 1 mutating requests and 1 responses" in written["notes"]


# --- a failure the shell can see ------------------------------------------------------
#
# run() printed its verdict to stderr and returned 0. Every unusable run --
# nothing recorded, no response, no readable body -- looked like a success to
# anything that checked the exit code instead of reading the prose. The first
# live capture exited 0.


def _run_capture(tmp_path, monkeypatch, session):
    capture = _capture_module()
    monkeypatch.setattr(capture.browser, "BrowserOptions", lambda **kwargs: object())
    monkeypatch.setattr(capture.browser, "open_session", lambda _options: session)
    monkeypatch.setattr(capture, "wait_for_enter", lambda *a, **k: None)
    args = argparse.Namespace(
        out=str(tmp_path), profile=str(tmp_path / "p"), cdp_url="http://127.0.0.1:9222", yes=True
    )
    return capture.run(args), json.loads((tmp_path / "capture.json").read_text())


class _QuietSession:
    """A session where nothing happens: no requests, no responses."""

    def __init__(self):
        self.page = _PumpingPage()

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


def test_a_run_that_recorded_nothing_exits_nonzero(tmp_path, monkeypatch):
    status, written = _run_capture(tmp_path, monkeypatch, _QuietSession())

    assert status != 0, "a run with no mutating request reported failure and exited 0"
    assert written["mutating_requests"] == []


def test_a_run_whose_body_could_not_be_read_exits_nonzero(tmp_path, monkeypatch):
    url = f"{ORCHESTRA}/updateItems/45fca98d0f0fb2ffc2ccf8a3290b8914"
    request = CaptureRequest(url, '{"variables":{"input":{"items":[{"quantity":1}]}}}')
    session = _TeardownSession(request, CaptureResponse(url, request, text=None))

    status, written = _run_capture(tmp_path, monkeypatch, session)

    assert status != 0, "a 200 with an unreadable body is not a success"
    assert written["responses"][0]["body_shape"] == _capture_module().UNREADABLE


def test_a_run_that_got_what_it_came_for_exits_zero(tmp_path, monkeypatch):
    url = f"{ORCHESTRA}/updateItems/45fca98d0f0fb2ffc2ccf8a3290b8914"
    request = CaptureRequest(url, '{"variables":{"input":{"items":[{"quantity":1}]}}}')
    session = _TeardownSession(request, CaptureResponse(url, request, text='{"data":{}}'))

    status, _ = _run_capture(tmp_path, monkeypatch, session)

    assert status == 0


# --- Enter means the clicking is done, not the network --------------------------------
#
# wait_for_enter returns the moment stdin answers. A response still in flight
# then met a closing context, which is the unreadable-at-teardown failure by
# another route -- and the human has no way to know whether the answer landed.


def _settling_recorder(tmp_path):
    recorder = _recorder(tmp_path)
    request = _cleared_request(recorder)
    return recorder, request


def test_settling_waits_for_an_answer_that_is_still_in_flight(tmp_path):
    capture = _capture_module()
    recorder, request = _settling_recorder(tmp_path)
    page = _PumpingPage()
    assert recorder.pending == 1

    # The answer arrives during the settle window, as a slow reply would.
    def answer_after_two_pumps(ms):
        page.waits.append(ms)
        if len(page.waits) == 2:
            recorder.on_response(CaptureResponse(request.url, request, text='{"data":{}}'))

    page.wait_for_timeout = answer_after_two_pumps

    outstanding = capture.settle(page, recorder, seconds=5.0)

    assert outstanding == 0
    assert recorder.responses, "the late response was not recorded"


class _BoundedPage(_PumpingPage):
    """A page that spends the time it is asked to wait, and not forever.

    Written after the deadline test spun instead of failing when a mutant
    removed the deadline -- the same lesson as the pump test, twice in one
    afternoon. Two things are needed and the first attempt had only one. The
    count stops an unbounded loop. The sleep is what makes the count mean
    something: a fake that returns instantly lets a *correct* loop run
    thousands of times inside a real deadline, so the limit fired on working
    code. A fake standing in for something that waits has to wait.
    """

    LIMIT = 20

    def wait_for_timeout(self, ms):
        super().wait_for_timeout(ms)
        time.sleep(ms / 1000.0)
        if len(self.waits) > self.LIMIT:
            raise AssertionError(f"settling pumped {self.LIMIT} times: it has no deadline")


def test_settling_gives_up_at_the_deadline_rather_than_hanging(tmp_path):
    capture = _capture_module()
    recorder, _ = _settling_recorder(tmp_path)
    started = time.monotonic()

    outstanding = capture.settle(_BoundedPage(), recorder, seconds=0.3)

    assert outstanding == 1
    assert time.monotonic() - started < 5, "settling did not respect its deadline"


def test_an_unanswered_request_is_not_a_success():
    verdict = _capture_module().verdict
    problem = verdict(watched=True, mutations=1, responses=0, readable=0, pending=1)

    assert problem is not None
    assert "unanswered" in problem


class _LateAnswerSession:
    """Request arrives while waiting; the answer only arrives while settling.

    This is the shape of the real thing: Walmart replies a moment after the
    human has stopped clicking. A run that stops listening at Enter records the
    request and never the response.
    """

    def __init__(self, request, response):
        self.page = _PumpingPage()
        self._request, self._response = request, response
        self._handlers = {}
        self.page.wait_for_timeout = self._pump

    def _pump(self, ms):
        self.page.waits.append(ms)
        self._handlers["response"](self._response)

    def fire_request(self):
        self._handlers["request"](self._request)

    def on_worker(self, handler):
        self._handlers["worker"] = handler

    def on_request(self, handler):
        self._handlers["request"] = handler

    def on_response(self, handler):
        self._handlers["response"] = handler

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_response_arriving_after_enter_is_still_recorded(tmp_path, monkeypatch):
    capture = _capture_module()
    url = f"{ORCHESTRA}/updateItems/45fca98d0f0fb2ffc2ccf8a3290b8914"
    request = CaptureRequest(url, '{"variables":{"input":{"items":[{"quantity":1}]}}}')
    session = _LateAnswerSession(request, CaptureResponse(url, request, text='{"data":{}}'))

    monkeypatch.setattr(capture.browser, "BrowserOptions", lambda **kwargs: object())
    monkeypatch.setattr(capture.browser, "open_session", lambda _options: session)
    # Enter is pressed with the request sent and unanswered.
    monkeypatch.setattr(capture, "wait_for_enter", lambda *a, **k: session.fire_request())

    args = argparse.Namespace(
        out=str(tmp_path), profile=str(tmp_path / "p"), cdp_url="http://127.0.0.1:9222", yes=True
    )
    status = capture.run(args)
    written = json.loads((tmp_path / "capture.json").read_text())

    assert written["responses"], "the answer arrived after Enter and was thrown away"
    assert status == 0


# --- an abort is a more specific answer than a bad result -----------------------------


def test_an_unavailable_browser_keeps_its_own_exit_code(tmp_path, monkeypatch):
    """status 2 means the environment was wrong; 1 would say the run was merely useless."""
    capture = _capture_module()

    def unavailable(_options):
        raise capture.browser.BrowserUnavailable("no Chrome on 9222")

    monkeypatch.setattr(capture.browser, "BrowserOptions", lambda **kwargs: object())
    monkeypatch.setattr(capture.browser, "open_session", unavailable)

    args = argparse.Namespace(
        out=str(tmp_path), profile=str(tmp_path / "p"), cdp_url="http://127.0.0.1:9222", yes=True
    )

    assert capture.run(args) == 2
