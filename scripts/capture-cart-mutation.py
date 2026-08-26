#!/usr/bin/env python3
"""Record the shape of Walmart's answer when a human changes their own cart.

What is known: the updateItems request. Four were observed at browser level --
the path, the persisted-query hash, the eighteen feature flags, and the absence
of both operationName and lineItemId. That contract is written up in
docs/CONTRACTS.md.

What is not known, and why this exists: the response. Nothing has ever been
seen coming back, so the tool cannot tell a change that worked from one that
silently did nothing, and `cart add` and `cart remove` refuse before opening a
browser. Per-line delivery eligibility is unobserved too, and is a separate
gate. Neither opens without evidence.

The requests come from the page. An earlier version of this file said they were
issued from a Web Worker; that was inferred from page-context hooks seeing
nothing, and browser-level events later recorded all four with from_worker
false. Silence from an instrument is a fact about the instrument.

NOTHING HERE DRIVES THE BROWSER. The human clicks; we watch. That is the whole
design: a mutation we have never observed must not be guessed at, and the
safest way to observe one is to let the person whose cart it is perform it --
one quantity change, to a line already in their cart, of their choosing.

WHAT IS WRITTEN DOWN is metadata and structure, and no bodies:

    request:  url, method, resource_type, from_worker, operation, body_shape
    response: url, operation, status, ok, body_shape

No headers, no request body, no response body. A shape is the document with
every value replaced by its type and every key not already known to us replaced
by a count. A body that will not parse is named, not copied.

Nothing here is promotable by scripts/promote-fixture.py, which scrubs and
promotes captured documents: there is no document to promote. A shape may be
quoted into a test or a contract by hand, which is what it is for.

Output goes to the gitignored recon directory, 0600.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import itertools
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


# Bounds on the shape walk. A response nobody has seen has no known depth,
# breadth or length, and an unbounded walk over one is a way to hang the
# capture or to write a file the size of the cart.
MAX_SHAPE_DEPTH = 12
MAX_LIST_ITEMS = 25
MAX_DICT_ITEMS = 50
MAX_VARIANTS = 5

# Keys are the only part of a shape copied verbatim, so for the first
# value-free run they come from a list, not from a pattern.
#
# A pattern was not enough. "Identifier-shaped and short" describes a GraphQL
# field, and it equally describes `Eric`, `ashley`, `household1` and any short
# opaque token, so a map keyed by a first name would have been written out in
# full. The earlier canaries used an email and a UUID, both of which fail the
# pattern for reasons that have nothing to do with the claim being tested.
#
# Everything here is a key we have already seen, and nothing here can be a
# person, a household or an id. Anything else -- however well-formed -- is
# collapsed and counted. A high unknown count is not a failure of the capture;
# it is the capture telling us a second, targeted observation is needed.
ALLOWED_SHAPE_KEYS = frozenset(
    (
        # The GraphQL envelope, which is the specification, not Walmart's data.
        "data",
        "errors",
        "extensions",
        "message",
        "path",
        "locations",
        "line",
        "column",
        "code",
        "__typename",
        # The operation this capture exists for.
        "updateItems",
        # Keys observed in the captured updateItems request. Field names only:
        # the value behind every one of these is still replaced by its type.
        "variables",
        "input",
        "cart",
        "cartId",
        "items",
        "offerId",
        "quantity",
        "usItemId",
        "name",
        "isPharmacyPrescription",
        "isQSRItem",
        "features",
        "skipPolicyCheck",
        "cartLeanMode",
        "enableCartSplitClarity",
        "enableLiquorBox",
    )
)

# What a key that is not on the list becomes. The text is dropped, the fact
# that there were some is kept, because a map keyed by id is itself a shape.
DYNAMIC_KEYS = "<dynamic keys>"

# Neither can collide with a real key: no observed field name is bracketed.
TRUNCATED = "<truncated>"


def is_known_key(key: object) -> bool:
    return isinstance(key, str) and key in ALLOWED_SHAPE_KEYS


def _distinct(shapes: list[Any]) -> tuple[list[Any], bool]:
    """Unique shapes, in order, capped. Returns (shapes, truncated).

    Looking only at element zero was not safe to promote as a contract: the
    line we care about may not be first, and an errors array is heterogeneous
    by nature. Keeping every element instead would copy the response's size
    into the evidence file, so it is bounded and says when it truncated.
    """
    seen: dict[str, Any] = {}
    truncated = False
    for shape in shapes:
        key = json.dumps(shape, sort_keys=True, default=str)
        if key in seen:
            continue
        if len(seen) >= MAX_VARIANTS:
            truncated = True
            break
        seen[key] = shape
    return list(seen.values()), truncated


def _collapse(shapes: list[Any], count: int, label: str) -> dict[str, Any]:
    distinct, truncated = _distinct(shapes)
    described: dict[str, Any] = {label: count}
    if len(distinct) == 1:
        described["of"] = distinct[0]
    elif distinct:
        described["variants"] = distinct
    if truncated or count > MAX_LIST_ITEMS:
        described["truncated"] = True
    return described


# A body that would not parse. Named rather than kept: the text itself is not
# retained anywhere, on either side.
UNPARSEABLE = "<unparseable>"


def _shape(payload: Any) -> Any:
    """shape_of, except that the unparseable sentinel stays itself.

    Passing it through shape_of would describe it as "str", which is true of
    the sentinel and says nothing about the body.
    """
    return UNPARSEABLE if payload is UNPARSEABLE else shape_of(payload)


def shape_of(value: Any, depth: int = 0) -> Any:
    """The structure of a payload with every value -- and every dynamic key -- removed.

    Separate from scrubbing, and stricter. Scrubbing removes what we know to be
    sensitive; this removes everything, so what is left cannot leak by being
    something nobody thought to add to a list of key names. It is the form the
    contract can be written down in.

    Keys are the exception that had to be closed twice. An earlier version kept
    every key, which is only value-free until a response is keyed by email or
    order id. The version after that kept identifier-shaped keys, which is only
    value-free until one is keyed by a first name. Keys now come from a list of
    field names already observed, and everything else collapses into one marker
    that counts them without quoting them.

    Booleans are named apart from numbers, because bool is an int in Python and
    that distinction has already caused two defects here.
    """
    if depth >= MAX_SHAPE_DEPTH:
        return "..."
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        # One budget for the whole object, spent before anything is looked at.
        #
        # The previous version capped what it recursed into and what it stored,
        # then iterated every entry anyway to count the unknown ones -- so a
        # million-key object still cost a million iterations, which is the cost
        # the cap was written to avoid. It also gave known and unknown keys a
        # budget each, so the stated cap allowed twice as many shaped children
        # as it said; harmless while the allowlist is short, and wrong the day
        # it grows.
        #
        # Missing a known field that arrives after the cutoff is honest,
        # because the marker says the object was truncated. Scanning an
        # unbounded object to make sure we did not miss it is not.
        total = len(value)
        described: dict[str, Any] = {}
        dynamic: list[Any] = []
        dynamic_count = 0
        for key, item in itertools.islice(value.items(), MAX_DICT_ITEMS):
            if is_known_key(key):
                described[key] = shape_of(item, depth + 1)
            else:
                dynamic_count += 1
                dynamic.append(shape_of(item, depth + 1))
        if dynamic_count:
            described[DYNAMIC_KEYS] = _collapse(dynamic, dynamic_count, "count")
        if total > MAX_DICT_ITEMS:
            described[TRUNCATED] = {"total": total}
        return described
    if isinstance(value, list):
        # An empty list needs no special case: _collapse describes nothing as
        # {"list": 0}. It had one, and mutating it away changed no behaviour and
        # broke no test -- which is what an equivalent mutant is for.
        shapes = [shape_of(item, depth + 1) for item in value[:MAX_LIST_ITEMS]]
        return _collapse(shapes, len(value), "list")
    return type(value).__name__


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

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.events: list[dict[str, Any]] = []
        self.workers: list[str] = []
        self.mutations = 0
        self.skipped = 0
        self.refused = 0
        self.unclassified = 0
        self.responses: list[dict[str, Any]] = []
        self.unreadable_responses = 0
        self.uncorrelated_responses = 0
        # The requests we cleared and recorded, held by identity. A response is
        # only read if it answers one of these. Holding the objects also keeps
        # them alive, so identity stays meaningful for the life of the capture.
        self._cleared: dict[int, Any] = {}

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
        # Metadata and shape. Not the headers, and not the body.
        #
        # This run exists for the response. The request contract was captured
        # before and has not changed, so keeping the body again would add the
        # product Eric chose, its identifiers and its quantity, and would add
        # no evidence. Headers go for the same reason: the drop list covers the
        # ones we know, and this is a request nobody has re-read since.
        record = {
            "url": scrub.scrub_url(url),
            "method": method,
            "resource_type": getattr(request, "resource_type", None),
            # The point of the whole exercise: which target issued it.
            "from_worker": self._from_worker(request),
            # The name the guard cleared, not a second opinion. This field
            # used to be computed by a separate helper that fell back to the
            # last path segment -- which, for a persisted query, is the hash
            # after the operation. Every request in the first real capture was
            # therefore labelled with a 64-character hash. The guard was right
            # and the evidence file was wrong, which is the more dangerous way
            # round: nobody rereads a document they believe was validated.
            "operation": operation,
            "body_shape": _shape(decoded),
        }
        self.events.append(record)
        self._cleared[id(request)] = request
        origin = "worker" if record["from_worker"] else "page"
        print(f"  [{self.mutations}] {method} {record['operation'] or record['url']}  ({origin})")

    def on_response(self, response) -> None:
        """Record the answer to a request we already cleared, and nothing else.

        The response side is where the remaining unknown lives: no successful
        updateItems body has ever been observed, so the cart mutation refuses
        to run at all. This is the evidence that would change that.

        It is deliberately narrower than the request side. A request is judged
        on what it is; a response is judged on what it answers. Only responses
        to requests that already passed every check are read, and the checks
        are then repeated on the response's own URL rather than inherited --
        correlation says which request this belongs to, it does not say the
        request was allowed. An order-flow response body is never fetched, not
        even to discard it: reading it would put it in this process.
        """
        request = getattr(response, "request", None)
        url = getattr(response, "url", "") or ""

        if not is_walmart(url):
            return
        operation = safety.operation_of(url)
        if safety.is_forbidden_url(url) or safety.is_forbidden_operation(operation):
            # Counted, never opened.
            self.refused += 1
            return
        if operation not in ALLOWED_OPERATIONS:
            return
        if request is None or id(request) not in self._cleared:
            # An allowed operation we did not send or did not clear. Worth
            # knowing about -- it means the request hook missed something --
            # but not worth reading.
            self.uncorrelated_responses += 1
            return

        status = getattr(response, "status", None)
        try:
            payload = self._decode(response.text())
        except Exception:  # noqa: BLE001 - a body we cannot read is still a finding
            self.unreadable_responses += 1
            payload = None
            print(f"  response {status}: body could not be read")
        else:
            print(f"  response {status}: {self._summarise(payload)}")

        # The request has been answered, so stop holding it. Nothing later in
        # the session needs it, and a correlated response arrives once.
        self._cleared.pop(id(request), None)

        # The shape only. The body exists in this process for as long as it
        # takes to derive that, and is not written down.
        #
        # An updateItems response has never been seen. It may well carry a
        # whole cart, or the household behind it. A scrubber works from a list
        # of field names, and no list can be shown to recognise fields nobody
        # has looked at yet -- so the first run keeps the structure and none of
        # the data. If the structure turns out not to be enough, the way to
        # learn more is another observation with named paths approved in
        # advance, not a copy of everything taken now in case.
        self.responses.append(
            {
                "url": scrub.scrub_url(url),
                "operation": operation,
                "status": status,
                "ok": getattr(response, "ok", None),
                "body_shape": _shape(payload),
            }
        )
        del payload

    @staticmethod
    def _summarise(payload: Any) -> str:
        if payload is UNPARSEABLE:
            return "body did not parse"
        if not isinstance(payload, dict):
            return "not an object"
        parts = []
        if payload.get("errors"):
            parts.append("errors present")
        data = payload.get("data")
        if isinstance(data, dict):
            # Schema keys only, for the same reason the shape filters them: a
            # key can be an id, and this goes to a terminal and a scrollback.
            named = sorted(key for key in data if is_known_key(key))
            parts.append(f"data keys: {', '.join(named) or 'none'}")
        elif "data" in payload:
            parts.append("data is not an object")
        return "; ".join(parts) or "no data and no errors"

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

    @staticmethod
    def _decode(body: str | None) -> Any:
        """Parsed JSON, or a sentinel. Never a copy of the text.

        This used to fall back to scrubbing the raw text, which made a second
        sensitive-derived string out of a body we could not even read -- only
        for the shape walk to reduce it to "str". A body that will not parse
        is refused on the request side and described as unparseable on the
        response side, and neither needs the text kept.
        """
        if not body:
            return None
        try:
            return json.loads(body)
        except (TypeError, ValueError):
            return UNPARSEABLE

    def write(self, notes: list[str]) -> Path:
        document = {
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "what": "cart mutation performed by hand in the user's own browser",
            "workers_seen": [scrub.scrub_url(url) for url in self.workers],
            "mutating_requests": self.events,
            "responses": self.responses,
            "responses_not_read_because_uncorrelated": self.uncorrelated_responses,
            "responses_that_could_not_be_read": self.unreadable_responses,
            "refused_order_flow_requests": self.refused,
            "requests_not_recorded_because_unasked_for": self.skipped,
            "requests_not_recorded_because_unrecognised_shape": self.unclassified,
            "recorded_operations": sorted(ALLOWED_OPERATIONS),
            "notes": notes,
        }
        return write_private(self.out_dir / "capture.json", json.dumps(document, indent=2) + "\n")


def confirm(out_dir: Path) -> bool:
    print("This attaches to your own Chrome and records while YOU click.")
    print("It never navigates and never clicks. An order flow is not recorded:")
    print("its body is never read and it is counted as refused.")
    print("It does not stop: you are driving, and stopping is your decision.")
    print(f"Output (gitignored, 0600): {out_dir}")
    print()
    print("One observation is the goal, so keep it to one:")
    print("  1. open your existing cart in the tab that opens")
    print("  2. change the quantity of ONE line you already have, once")
    print("  3. come back here and press Enter, then put it back yourself")
    print()
    print("Editing a line already in the cart avoids introducing a new item, a")
    print("new variant, or a second mutation to explain. Which line, and how you")
    print("restore it, are yours to choose.")
    print()
    print("Recorded: the request's metadata and shape, and the SHAPE of the")
    print("response -- structure with every value and every unrecognised key")
    print("removed. Neither body is written down, and no headers are kept.")
    print()
    return input("start recording? [y/N] ").strip().lower() in ("y", "yes")


def verdict(watched: bool, mutations: int, responses: int) -> str | None:
    """What the run amounted to, or None if it got what it came for.

    A function rather than a chain of prints so it can be tested. The three
    negative answers are deliberately distinct: a session that never opened is
    not a finding about Walmart, and requests without responses is a narrower
    failure than silence -- the hook is attached and the answers are going
    somewhere we cannot see.
    """
    if not watched:
        return "nothing was recorded because the session never opened."
    if mutations == 0:
        return (
            "No mutating request was recorded. That is itself a finding: either no "
            "add happened, or Playwright is not reporting the worker's traffic and "
            "we need a raw CDP attach instead."
        )
    if responses == 0:
        return (
            f"{mutations} request(s) recorded but no response was. The response side "
            "is the whole point of this run, so this is a failure, not a quiet success."
        )
    return None


def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out) if args.out else default_out_dir()
    if not args.yes and not confirm(out_dir):
        print("aborted", file=sys.stderr)
        return 2

    recorder = Recorder(out_dir)
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
            session.on_response(recorder.on_response)
            print("\nrecording - drive the browser yourself; do not enter an order flow")
            print("press Enter here when you are done\n")
            watched = True
            with contextlib.suppress(EOFError):
                input()
            notes.append(
                f"recorded {recorder.mutations} mutating requests and {len(recorder.responses)} responses"
            )
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
            f"\n{recorder.mutations} mutating request(s), "
            f"{len(recorder.responses)} response(s), {len(recorder.workers)} worker(s)"
        )
        print(f"wrote {path}")
        problem = verdict(watched, recorder.mutations, len(recorder.responses))
        if problem:
            print(problem, file=sys.stderr)

    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--profile", default=str(Path.home() / ".local/share/walmart/attached-chrome"))
    parser.add_argument("--out")
    parser.add_argument("--yes", action="store_true")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
