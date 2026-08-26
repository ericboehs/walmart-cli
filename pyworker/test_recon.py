"""Recon recorder tests. Nothing here contacts Walmart: the session is a double.

scripts/recon.py is the only tool in this repo that touches the live site on
purpose, so the parts that decide *whether* it runs and *what it leaves behind*
are worth testing offline.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
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
