#!/usr/bin/env python3
"""Record what Walmart sends when a human adds an item to their own cart.

The one contract in this project we have never seen. `cart.add`,
`cart.increment`, `cart.decrement` and `cart.remove` are reserved names in the
worker protocol and every one of them is a not_implemented stub, because the
update is issued from a Web Worker and the JS hooks used for every other
surface run in page context, where a worker's fetch is invisible.

That was a fact about the hook, not about the request. Playwright attaches to
dedicated worker targets and reports their network events on the owning page,
so this records at that level instead.

NOTHING HERE DRIVES THE BROWSER. The human clicks; we watch. That is the whole
design: a mutation we have never observed must not be guessed at, and the
safest way to observe one is to let the person whose cart it is perform it.

Output goes to the gitignored recon directory, scrubbed, 0600. Nothing captured
here is a fixture until it has been through scripts/promote-fixture.py.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pyworker"))

import browser  # noqa: E402
import safety  # noqa: E402
import scrub  # noqa: E402

# Only bodies going to Walmart's own API are recorded. Everything else in a
# real browser tab is someone's private browsing.
INTERESTING_HOSTS = ("walmart.com",)

# A mutation is a POST. GETs are recorded as one summary line, never a body:
# they are page furniture and they are also where the tracking beacons live.
BODY_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def default_out_dir() -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return REPO_ROOT / "pyworker" / "recon" / f"cart-mutation-{stamp}"


def write_private(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w") as out:
        out.write(text)
    return path


def host_of(url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower()


def is_walmart(url: str) -> bool:
    host = host_of(url)
    return any(host == name or host.endswith("." + name) for name in INTERESTING_HOSTS)


class Recorder:
    """Everything we saw, with the bodies of anything that changed state."""

    def __init__(self, out_dir: Path, literals: list[str]):
        self.out_dir = out_dir
        self.literals = literals
        self.events: list[dict[str, Any]] = []
        self.workers: list[str] = []
        self.mutations = 0

    def on_worker(self, worker) -> None:
        url = getattr(worker, "url", "") or "(unknown)"
        self.workers.append(url)
        print(f"  worker started: {scrub.scrub_url(url)}")

    def on_request(self, request) -> None:
        url = getattr(request, "url", "") or ""
        if not is_walmart(url):
            return
        method = (getattr(request, "method", "") or "").upper()
        if method not in BODY_METHODS:
            return

        # A capture that navigated somewhere forbidden would be a capture we
        # must delete. Refuse loudly instead.
        if safety.is_forbidden_url(url):
            raise safety.UnsafeNavigation(f"refusing to record a forbidden URL: {url}")

        body = None
        try:
            body = request.post_data
        except Exception:  # noqa: BLE001 - a body we cannot read is still a finding
            body = None

        self.mutations += 1
        record = {
            "url": scrub.scrub_url(url),
            "method": method,
            "resource_type": getattr(request, "resource_type", None),
            # The point of the whole exercise: which target issued it.
            "from_worker": self._from_worker(request),
            "headers": scrub.scrub_headers(dict(getattr(request, "headers", {}) or {})),
            "operation": self._operation(body, url),
            "body": scrub.scrub_json(self._decode(body), self.literals),
        }
        self.events.append(record)
        origin = "worker" if record["from_worker"] else "page"
        print(
            f"  [{self.mutations}] {method} {record['operation'] or record['url']}  ({origin})"
        )

    def _from_worker(self, request) -> bool | None:
        """True when Playwright attributes this request to a worker.

        None when the build does not tell us. Recorded either way, because
        "we could not tell" and "it came from the page" are different answers
        and only one of them means the hypothesis was wrong.
        """
        frame = getattr(request, "frame", None)
        if frame is None:
            return True
        try:
            frame.url  # noqa: B018 - a page request has a live frame; a worker's does not
        except Exception:  # noqa: BLE001
            return True
        return False

    def _operation(self, body: str | None, url: str) -> str | None:
        decoded = self._decode(body)
        if isinstance(decoded, dict):
            name = decoded.get("operationName")
            if isinstance(name, str) and name:
                return name
        if "/orchestra/" in url:
            return url.split("?")[0].rsplit("/", 1)[-1]
        return None

    def _decode(self, body: str | None) -> Any:
        if not body:
            return None
        try:
            return json.loads(body)
        except (TypeError, ValueError):
            return scrub.scrub_text(body, self.literals)

    def write(self, notes: list[str]) -> Path:
        document = {
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "what": "cart mutation performed by hand in the user's own browser",
            "workers_seen": [scrub.scrub_url(url) for url in self.workers],
            "mutating_requests": self.events,
            "notes": notes,
        }
        return write_private(
            self.out_dir / "capture.json", json.dumps(document, indent=2) + "\n"
        )


def confirm(out_dir: Path) -> bool:
    print("This attaches to your own Chrome and records while YOU click.")
    print("It never navigates and never clicks. Anything the safety guard forbids aborts it.")
    print(f"Output (gitignored, 0600): {out_dir}")
    print()
    print("Do this after it says 'recording':")
    print("  1. go to any Walmart product page in the tab that opens")
    print("  2. click Add to cart, once")
    print("  3. change the quantity, then remove it again if you like")
    print("  4. come back here and press Enter")
    print()
    return input("start recording? [y/N] ").strip().lower() in ("y", "yes")


def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out) if args.out else default_out_dir()
    if not args.yes and not confirm(out_dir):
        print("aborted", file=sys.stderr)
        return 2

    literals = [line.strip() for line in (args.scrub_literal or []) if line.strip()]
    recorder = Recorder(out_dir, literals)
    options = browser.BrowserOptions(
        profile_dir=args.profile,
        profile_lock=args.profile + ".lock",
        headless=False,
        cdp_url=args.cdp_url,
    )

    notes: list[str] = []
    status = 0
    try:
        with browser.open_session(options) as session:
            session.on_worker(recorder.on_worker)
            session.on_request(recorder.on_request)
            print(
                "\nrecording - drive the browser yourself; do not enter an order flow"
            )
            print("press Enter here when you are done\n")
            with contextlib.suppress(EOFError):
                input()
            notes.append(f"recorded {recorder.mutations} mutating requests")
    except safety.UnsafeNavigation as exc:
        notes.append(f"aborted: {exc}")
        print(f"refused: {exc}", file=sys.stderr)
        status = 2
    except browser.BrowserUnavailable as exc:
        notes.append(f"browser unavailable: {exc}")
        print(f"browser unavailable: {exc}", file=sys.stderr)
        status = 2
    except KeyboardInterrupt:
        notes.append("interrupted")
        status = 2
    finally:
        path = recorder.write(notes)
        print(
            f"\n{recorder.mutations} mutating request(s), {len(recorder.workers)} worker(s)"
        )
        print(f"wrote {path}")
        if recorder.mutations == 0:
            print(
                "No mutating request was recorded. That is itself a finding: either no "
                "add happened, or Playwright is not reporting the worker's traffic and "
                "we need a raw CDP attach instead.",
                file=sys.stderr,
            )

    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument(
        "--profile", default=str(Path.home() / ".local/share/walmart/attached-chrome")
    )
    parser.add_argument("--out")
    parser.add_argument("--scrub-literal", action="append")
    parser.add_argument("--yes", action="store_true")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
