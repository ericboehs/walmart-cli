#!/usr/bin/env python3
"""Live reconnaissance recorder.

Answers DESIGN.md's ten recon questions with observation instead of assumption.
It opens the dedicated browser profile, visits (or lets a human drive) a set of
read-only surfaces, and records what the real web app actually does.

Guard rails:
  * it will not start without an explicit --live flag and a typed confirmation,
    because it is the only tool here that touches Walmart on purpose;
  * every navigation goes through the guard in pyworker/safety.py;
  * captures are scrubbed as they are written unless --keep-raw is given, and
    the default output directory (pyworker/recon/) is gitignored; --out
    somewhere else is on you;
  * nothing is ever mutated: no clicking on the human's behalf, no cart writes.

Usage:
    scripts/recon.py --live search
    scripts/recon.py --live observe --seconds 300
    scripts/recon.py --list
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = Path(__file__).resolve()
sys.path.insert(0, str(REPO_ROOT / "pyworker"))

import browser  # noqa: E402
import recon_plans  # noqa: E402
import safety  # noqa: E402
import scrub  # noqa: E402

INTERESTING_TYPES = ("application/json", "text/html", "application/graphql")
MAX_BODY_BYTES = 4_000_000

# Third-party plumbing that costs a lot of capture and teaches nothing about
# Walmart's contracts. Partytown is a web worker that proxies other people's
# scripts: it was 321 of 395 bodies in the search run, 81% of the capture, and
# not one of them was an endpoint we could ever call.
#
# Bot-protection traffic is deliberately NOT in this list. PerimeterX and
# ThreatMetrix are noise by volume and evidence by nature -- they are how we
# know what gates the login flow -- so they stay.
NOISE_URL_FRAGMENTS = ("/~partytown/",)
DEFAULT_OBSERVE_SECONDS = 180
# Longest path segment kept verbatim in a filename before it is digested.
MAX_SEGMENT_CHARS = 40
# Short enough that Ctrl-C is answered promptly and progress can be printed;
# long enough that we are not spinning through Playwright for no reason.
PUMP_SLICE_SECONDS = 2.0
PROGRESS_EVERY_SECONDS = 30.0


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")


def default_profile() -> Path:
    return xdg_data_home() / "walmart" / "browser-profile"


def default_out_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / "pyworker" / "recon" / stamp


def write_private(path: Path, text: str) -> Path:
    """Write a file that is never more permissive than 0600, even for an instant.

    Write-then-chmod leaves the file at the umask's mercy in between, and for a
    capture that gap covers a whole response body. Opening with the mode closes
    it; the chmod stays because open's mode is itself filtered by umask.
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    path.chmod(0o600)
    return path


def slugify(url: str) -> str:
    """A filename that describes the capture without leaking its query string.

    Only host and path contribute. The first live run put the leading 120
    characters of a signed token into a filename because the whole URL was the
    input; a filename is not a place to store data. Long opaque path segments
    (persisted-query hashes, for instance) collapse to a short digest so the
    name stays readable and stable.
    """
    split = urlsplit(url)
    segments = [_short_segment(part) for part in split.path.split("/") if part]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", "-".join([split.netloc, *segments]))
    if split.query:
        slug += "-q" + hashlib.sha256(split.query.encode()).hexdigest()[:8]
    return slug[:120].strip("-") or "response"


def _short_segment(segment: str) -> str:
    if len(segment) <= MAX_SEGMENT_CHARS:
        return segment
    return f"{segment[:8]}~{hashlib.sha256(segment.encode()).hexdigest()[:8]}"


# Persisted GraphQL: /orchestra/<app>/graphql/<OperationName>/<sha256>. The
# operation name is in the path, not in a POST body, which is why the first
# capture reported no operations at all for 42 GraphQL calls.
PERSISTED_GRAPHQL = re.compile(r"/orchestra/(?P<app>[A-Za-z0-9_-]+)/graphql/(?P<operation>[A-Za-z0-9_]+)")


class Recorder:
    """Writes one file per interesting response, plus an index."""

    def __init__(self, out_dir: Path, keep_raw: bool, literals: list[str]):
        self.out_dir = out_dir
        self.keep_raw = keep_raw
        self.literals = literals
        self.entries: list[dict] = []
        self.counter = 0
        # Skipping quietly would make the capture lie by omission, so what was
        # dropped is counted and reported.
        self.skipped: dict[str, int] = {}
        (self.out_dir / "bodies").mkdir(parents=True, exist_ok=True)
        # A capture is somebody's session in text form. Owner-only, and private
        # from the moment the directory exists rather than from whenever we
        # remember to tighten it.
        self.out_dir.chmod(0o700)
        (self.out_dir / "bodies").chmod(0o700)

    def on_response(self, response) -> None:
        try:
            content_type = (response.headers or {}).get("content-type", "")
            if not any(kind in content_type for kind in INTERESTING_TYPES):
                return
            if safety.is_forbidden_url(response.url):
                # Safety gets first look even when a URL otherwise matches a
                # noise rule: forbidden traffic is evidence, never clutter.
                self.entries.append({"url": self.safe_url(response.url), "note": "forbidden url observed"})
                self.warn("observed a forbidden URL")
                return
            noise = _noise_fragment(response.url)
            if noise:
                self.skipped[noise] = self.skipped.get(noise, 0) + 1
                return

            body = response.body()
            if not body or len(body) > MAX_BODY_BYTES:
                return
            text = body.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - a body we cannot read is not fatal
            self.entries.append({"url": self.safe_url(getattr(response, "url", "?")), "error": str(exc)})
            self.warn(f"could not read a response body: {exc}")
            return

        self.counter += 1
        extension = "json" if "json" in content_type else "html"
        name = f"{self.counter:03d}-{slugify(response.url)}.{extension}"

        # Scrubbing and writing are inside the guard too. A scrubber that throws
        # must never leave an unscrubbed body on disk, and a write that fails
        # must not be silently missing from a capture we later trust.
        try:
            payload = text if self.keep_raw else scrub.scrub_document(text, self.literals)
            write_private(self.out_dir / "bodies" / name, payload)
        except Exception as exc:  # noqa: BLE001
            self.counter -= 1
            self.entries.append(
                {"url": self.safe_url(response.url), "error": f"scrub/write failed: {exc}", "saved": False}
            )
            self.warn(f"NOT saved (scrub or write failed): {exc}")
            return

        request = response.request
        self.entries.append(
            {
                "file": f"bodies/{name}",
                "url": self.safe_url(response.url),
                "status": response.status,
                "method": request.method,
                "resource_type": request.resource_type,
                "content_type": content_type,
                "bytes": len(body),
                "request_headers": scrub.scrub_headers(dict(request.headers or {})),
                "response_headers": scrub.scrub_headers(dict(response.headers or {})),
                "graphql_operation": self.graphql_operation(request),
            }
        )

    def safe_url(self, url: str | None) -> str:
        """Recorded URLs go through the scrubber unless --keep-raw was given."""
        if self.keep_raw:
            return url or ""
        return scrub.scrub_url(url)

    def warn(self, message: str) -> None:
        """Immediate, visible: a recon run that quietly drops data is worthless."""
        print(f"warning: {message}", file=sys.stderr, flush=True)

    def graphql_operation(self, request) -> str | None:
        """GraphQL operation name, from the URL path first and the body second.

        Walmart uses persisted queries: the operation is a path segment and the
        request usually carries no body at all. Reading only post_data reported
        "no operation" for all 42 GraphQL calls in the first live capture.
        """
        url = getattr(request, "url", "") or ""
        match = PERSISTED_GRAPHQL.search(url)
        if match:
            return f"{match.group('app')}/{match.group('operation')}"

        try:
            if "graphql" not in url.lower():
                return None
            post = request.post_data
            if not post:
                return None
            data = json.loads(post)
            if isinstance(data, list):
                return ",".join(str(item.get("operationName")) for item in data)
            return data.get("operationName")
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
            self.warn(f"could not read a GraphQL operation name: {exc}")
            return None

    def write_index(self, plan: recon_plans.Plan, notes: list[str]) -> Path:
        index = {
            "plan": plan.name,
            "description": plan.description,
            "questions": plan.questions,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "scrubbed": not self.keep_raw,
            "notes": notes,
            "entries": self.entries,
            "skipped_as_noise": dict(sorted(self.skipped.items())),
        }
        path = self.out_dir / "index.json"
        write_private(path, json.dumps(index, indent=2, sort_keys=True))
        self.write_summary(plan)
        return path

    def write_summary(self, plan: recon_plans.Plan) -> None:
        """A human-readable digest: what endpoints exist and how often."""
        by_endpoint: dict[str, dict] = {}
        for entry in self.entries:
            url = entry.get("url", "?")
            key = entry.get("graphql_operation") or re.sub(r"\?.*$", "", url)
            bucket = by_endpoint.setdefault(key, {"count": 0, "statuses": set(), "example": url})
            bucket["count"] += 1
            if entry.get("status"):
                bucket["statuses"].add(entry["status"])

        lines = [
            f"# recon: {plan.name}",
            "",
            plan.description,
            "",
            "## questions this run should answer",
            *[f"- {question}" for question in plan.questions],
            "",
            "## endpoints observed",
            "",
            "| count | statuses | endpoint |",
            "|------:|----------|----------|",
        ]
        for key, bucket in sorted(by_endpoint.items(), key=lambda kv: -kv[1]["count"]):
            statuses = ",".join(str(status) for status in sorted(bucket["statuses"]))
            lines.append(f"| {bucket['count']} | {statuses} | `{key}` |")
        if self.skipped:
            lines += ["", "## skipped as third-party noise", ""]
            lines += [f"- {count}x `{fragment}`" for fragment, count in sorted(self.skipped.items())]
        lines += ["", "## findings", "", "_fill in before anything is promoted to a fixture_", ""]
        write_private(self.out_dir / "summary.md", "\n".join(lines))


def _noise_fragment(url: str | None) -> str | None:
    """Which noise rule matches this URL, if any."""
    for fragment in NOISE_URL_FRAGMENTS:
        if fragment in (url or ""):
            return fragment
    return None


def pump(session, seconds: float, countdown: bool = False) -> None:
    """Wait `seconds` while Playwright keeps dispatching response callbacks.

    Playwright's sync API has no background callback threads. Response handlers
    run on *this* thread, and only while it is parked inside a Playwright call.
    time.sleep() therefore does not wait quietly: it stops the recorder. Queued
    responses would sit undelivered until the next navigation, and anything
    arriving during the human-driven window would still be queued when the
    context closes -- which is most of an interactive recon run.

    wait_for_timeout is the pump. Slicing it keeps Ctrl-C responsive and lets a
    long observation window report that it is still alive.
    """
    remaining = max(0.0, float(seconds))
    page = session.page
    since_progress = 0.0

    while remaining > 0:
        step = min(PUMP_SLICE_SECONDS, remaining)
        page.wait_for_timeout(step * 1000)
        remaining -= step
        since_progress += step
        if countdown and since_progress >= PROGRESS_EVERY_SECONDS and remaining > 0:
            print(f"  {int(remaining)}s left", flush=True)
            since_progress = 0.0


# --- Interpreter -----------------------------------------------------------
#
# The first live attempt died before touching the network: the shebang resolved
# to the system Python, which has no playwright, so a run that a human had
# already consented to failed with a ModuleNotFoundError traceback and left a
# zero-body capture directory behind. The dependency lives in pyworker/.venv,
# and this script is the only entry point that does not go through the Ruby
# worker's interpreter, so it has to find it itself.

REEXEC_GUARD = "WALMART_RECON_REEXEC"
VENV_PYTHON = REPO_ROOT / "pyworker" / ".venv" / "bin" / "python"


def playwright_available() -> bool:
    """Is playwright importable here? Answered without importing it."""
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def project_interpreter() -> Path | None:
    """The project venv's python, if it exists and is not already running."""
    if not VENV_PYTHON.exists():
        return None
    try:
        if os.path.samefile(VENV_PYTHON, sys.executable):
            return None
    except OSError:
        pass
    return VENV_PYTHON


def maybe_reexec(argv: list[str], execve=os.execve) -> None:
    """Restart under the project interpreter when this one lacks playwright.

    Guarded by an environment variable: a venv that is itself missing the
    dependency would otherwise re-exec forever. Failing to re-exec is not fatal
    here -- --list works without a browser -- so the honest error is left to
    preflight_interpreter().
    """
    if os.environ.get(REEXEC_GUARD) or playwright_available():
        return
    interpreter = project_interpreter()
    if interpreter is None:
        return
    print(f"re-running under the project interpreter: {interpreter}", file=sys.stderr)
    env = dict(os.environ)
    env[REEXEC_GUARD] = "1"
    execve(str(interpreter), [str(interpreter), str(SCRIPT_PATH), *argv], env)


def preflight_interpreter() -> str | None:
    """Why this run cannot work, checked before we ask a human to consent to it.

    Consent is for the network traffic, not for a traceback. Anything knowable
    beforehand is checked beforehand.
    """
    if playwright_available():
        return None
    lines = [
        f"playwright is not installed for this interpreter ({sys.executable}).",
        "Install it in the project venv and re-run:",
        "    cd pyworker && uv sync && uv run playwright install chromium",
    ]
    if not VENV_PYTHON.exists():
        lines.insert(1, f"No project venv at {VENV_PYTHON}.")
    return "\n".join(lines)


def confirm_live(plan: recon_plans.Plan, out_dir: Path, keep_raw: bool) -> bool:
    print("About to run LIVE reconnaissance against walmart.com.")
    print(f"  plan:     {plan.name} - {plan.description}")
    print(f"  urls:     {', '.join(plan.urls) or '(human-driven)'}")
    print(f"  output:   {out_dir}  (gitignored)")
    print(f"  scrubbed: {'no - RAW capture' if keep_raw else 'yes, on write'}")
    print("  reads only: this tool never mutates a cart and never proceeds to an order.")
    try:
        answer = input("Type 'recon' to continue: ").strip()
    except EOFError:
        # A closed stdin is not consent, and it is not a crash either.
        print("aborted (no answer)", file=sys.stderr)
        return False
    return answer == "recon"


def run(args: argparse.Namespace) -> int:
    plan = recon_plans.PLANS[args.plan]
    out_dir = Path(args.out) if args.out else default_out_dir()

    if not args.live:
        print("refusing to run without --live (this contacts Walmart)", file=sys.stderr)
        return 2
    problem = preflight_interpreter()
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if not args.yes and not confirm_live(plan, out_dir, args.keep_raw):
        print("aborted", file=sys.stderr)
        return 2

    literals = [line.strip() for line in (args.scrub_literal or []) if line.strip()]
    recorder = Recorder(out_dir, args.keep_raw, literals)

    options = browser.BrowserOptions(
        profile_dir=str(args.profile or default_profile()),
        profile_lock=str(args.profile or default_profile()) + ".lock",
        headless=args.headless,
    )

    notes: list[str] = []
    status = 0
    # The index is the only record of what these bodies are and whether they
    # were scrubbed. Captures without it are exactly the untracked artifacts
    # invariant 9 exists to prevent, so it is written on every exit path.
    try:
        with browser.open_session(options) as session:
            session.on_response(recorder.on_response)

            for url in plan.urls:
                print(f"visiting {url}")
                try:
                    response = session.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    status_code = response.status if response else None
                    notes.append(f"{url} -> {status_code}")
                except safety.UnsafeNavigation:
                    raise
                except Exception as exc:  # noqa: BLE001 - a failed visit is a finding
                    notes.append(f"{url} -> error: {exc}")
                    print(f"  error: {exc}", file=sys.stderr)
                pump(session, args.settle)

            if plan.interactive or args.seconds:
                seconds = args.seconds or DEFAULT_OBSERVE_SECONDS
                print(f"recording for {seconds}s - drive the browser yourself; do not enter an order flow")
                pump(session, seconds, countdown=True)
    except safety.UnsafeNavigation as exc:
        notes.append(f"aborted early: navigation refused ({exc})")
        print(f"refused: {exc}", file=sys.stderr)
        status = 2
    except browser.BrowserUnavailable as exc:
        # A browser we could not start is an environment problem with a known
        # fix, not a bug worth a stack trace. The index is still written, so an
        # empty capture directory says why it is empty.
        notes.append(f"aborted early: browser unavailable ({exc})")
        print(f"browser unavailable: {exc}", file=sys.stderr)
        status = 2
    except KeyboardInterrupt:
        notes.append("aborted early: interrupted")
        print("interrupted", file=sys.stderr)
        status = 2
    finally:
        index = recorder.write_index(plan, notes)
        print(f"wrote {recorder.counter} bodies")
        print(f"index:   {index}")
        print(f"summary: {out_dir / 'summary.md'}")
        if args.keep_raw:
            print("WARNING: raw capture. Scrub before anything is promoted to pyworker/fixtures/.")

    return status


def main(argv: list[str] | None = None) -> int:
    maybe_reexec(list(sys.argv[1:] if argv is None else argv))
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("plan", nargs="?", choices=recon_plans.plan_names(), help="which surface to observe")
    parser.add_argument("--list", action="store_true", help="list plans and exit")
    parser.add_argument("--live", action="store_true", help="required: this contacts Walmart")
    parser.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    parser.add_argument("--headless", action="store_true", help="run headless (default: visible)")
    parser.add_argument("--profile", help="browser profile directory")
    parser.add_argument("--out", help="output directory (default: pyworker/recon/<timestamp>)")
    parser.add_argument("--seconds", type=int, default=0, help="extra human-driven recording time")
    parser.add_argument("--settle", type=float, default=4.0, help="seconds to wait after each visit")
    parser.add_argument("--keep-raw", action="store_true", help="skip scrubbing (not recommended)")
    parser.add_argument(
        "--scrub-literal",
        action="append",
        help="an extra literal string to redact (repeatable); never stored in the repo",
    )
    args = parser.parse_args(argv)

    if args.list or not args.plan:
        for name in recon_plans.plan_names():
            plan = recon_plans.PLANS[name]
            print(f"{name:10s} {plan.description}")
        return 0

    return run(args)


if __name__ == "__main__":
    sys.exit(main())
