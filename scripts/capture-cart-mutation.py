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

# The only operation this capture exists to record.
#
# An allowlist, not a denylist, and that is the whole design. The denylist in
# safety.py cannot enumerate an order operation Walmart has not shipped yet, so
# it is the alarm and this is the lock: a name nobody has seen before is not
# recorded, because we did not come here for it. The first run of this script
# recorded three order-flow operations, and no list of forbidden names would
# have been the reason it did not.
ALLOWED_OPERATIONS = ("updateItems",)


def is_update_items_body(body: object) -> bool:
    """Whether a decoded body has the shape updateItems is observed to send.

    A positive test, not the absence of a negative. Rejecting only bodies that
    *disagree* with the URL lets an unclassified body into the evidence file,
    because a body naming nothing disagrees with nothing.

    The discriminator is structural because none of the four observed requests
    carries operationName at all: Walmart uses persisted queries, so the
    operation lives in the URL path and the body carries only `variables`.
    Requiring operationName == updateItems would have recorded nothing. Where a
    body does carry one -- no observed request does, but the server accepts the
    field -- it must still agree, which safety.operation_mismatch checks.

    Observed shape: variables.input.items, a list of line entries.
    """
    if not isinstance(body, dict):
        return False
    variables = body.get("variables")
    if not isinstance(variables, dict):
        return False
    supplied = variables.get("input")
    if not isinstance(supplied, dict):
        return False
    return isinstance(supplied.get("items"), list)


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
        self.skipped = 0
        self.refused = 0
        self.unclassified = 0

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

        # Recorded only if we came here for it. A capture that held the order
        # flow is a capture we must delete, and deleting it afterwards means it
        # existed on disk first.
        #
        # This is not hypothetical. The first real run of this script recorded
        # three order-flow operations, because the person driving quite
        # reasonably kept clicking.
        operation = safety.operation_of(url)
        if safety.is_forbidden_url(url) or safety.is_forbidden_operation(operation):
            self.refused += 1
            print(f"  refused (order flow): {operation or scrub.scrub_url(url)}")
            return
        if operation not in ALLOWED_OPERATIONS:
            self.skipped += 1
            return

        body = None
        try:
            body = request.post_data
        except Exception:  # noqa: BLE001 - a body we cannot read is still a finding
            body = None

        # The path and the body must name the same operation. A request the
        # guard reads as updateItems and the server reads as something else is
        # a request we have no reason to send or to keep.
        decoded = self._decode(body)
        mismatch = safety.operation_mismatch(url, decoded)
        if mismatch:
            self.refused += 1
            print(f"  refused (body names {mismatch}, path names {operation})")
            return
        if not is_update_items_body(decoded):
            self.unclassified += 1
            print(f"  not recorded ({operation} body is not the shape we came for)")
            return

        self.mutations += 1
        record = {
            "url": scrub.scrub_url(url),
            "method": method,
            "resource_type": getattr(request, "resource_type", None),
            # The point of the whole exercise: which target issued it.
            "from_worker": self._from_worker(request),
            "headers": scrub.scrub_headers(dict(getattr(request, "headers", {}) or {})),
            # The name the guard cleared, not a second opinion. This field
            # used to be computed by a separate helper that fell back to the
            # last path segment -- which, for a persisted query, is the hash
            # after the operation. Every request in the first real capture was
            # therefore labelled with a 64-character hash. The guard was right
            # and the evidence file was wrong, which is the more dangerous way
            # round: nobody rereads a document they believe was validated.
            "operation": operation,
            "body": scrub.scrub_json(self._decode(body), self.literals),
        }
        self.events.append(record)
        origin = "worker" if record["from_worker"] else "page"
        print(f"  [{self.mutations}] {method} {record['operation'] or record['url']}  ({origin})")

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

    def _decode(self, body: str | None) -> Any:
        if not body:
            return None
        try:
            return json.loads(body)
        except (TypeError, ValueError):
            return scrub.scrub_text(body, self.literals)

    def write(self, notes: list[str]) -> Path:
        document = {
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "what": "cart mutation performed by hand in the user's own browser",
            "workers_seen": [scrub.scrub_url(url) for url in self.workers],
            "mutating_requests": self.events,
            "refused_order_flow_requests": self.refused,
            "requests_not_recorded_because_unasked_for": self.skipped,
            "requests_not_recorded_because_unrecognised_shape": self.unclassified,
            "recorded_operations": sorted(ALLOWED_OPERATIONS),
            "notes": notes,
        }
        return write_private(self.out_dir / "capture.json", json.dumps(document, indent=2) + "\n")


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
    # Whether we ever got as far as watching. Without this the summary below
    # reported "no mutating request was recorded" as a finding about Walmart
    # when the truth was that Chrome was not running -- a message that cannot
    # tell "we never looked" from "we looked and saw nothing" invents evidence.
    watched = False
    try:
        with browser.open_session(options) as session:
            session.on_worker(recorder.on_worker)
            session.on_request(recorder.on_request)
            print("\nrecording - drive the browser yourself; do not enter an order flow")
            print("press Enter here when you are done\n")
            watched = True
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
        print(f"\n{recorder.mutations} mutating request(s), {len(recorder.workers)} worker(s)")
        print(f"wrote {path}")
        if not watched:
            print("nothing was recorded because the session never opened.", file=sys.stderr)
        elif recorder.mutations == 0:
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
    parser.add_argument("--profile", default=str(Path.home() / ".local/share/walmart/attached-chrome"))
    parser.add_argument("--out")
    parser.add_argument("--scrub-literal", action="append")
    parser.add_argument("--yes", action="store_true")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
