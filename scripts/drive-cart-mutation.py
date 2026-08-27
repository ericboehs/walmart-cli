#!/usr/bin/env python3
"""Make one quantity change to one cart line, and record what Walmart answers.

This is the companion to scripts/capture-cart-mutation.py, which observes while
a human clicks. That one stays as it is: it drives nothing, and the promise is
worth keeping, because a recorder you can run without thinking is a different
tool from one that acts. This script acts, deliberately, and says so.

WHY IT EXISTS. The updateItems request has been observed. The response never
has, so `walmart cart add` and `cart remove` refuse before they open a browser:
there is no way to tell a change that worked from one that silently did
nothing. That gate does not open on argument. It opens on a recorded response
shape, and getting one means a real change to a real cart.

WHAT IT DOES. Attaches to a Chrome you started and signed into, opens the cart,
finds one line by item id, clicks that line's quantity control exactly once,
waits for the answer, and puts the quantity back. Two mutations, both of them
yours, ending where they started.

WHAT IT WILL NOT DO. The cart is the only page it opens. Every navigation goes
through safety.assert_navigable and every click through assert_clickable, so a
control whose accessible name reads like an order flow is refused however the
page is laid out. It never acts on a control it cannot find exactly once: if
the page does not look the way it expects, it stops and tells you to run the
human-driven capture instead. It does not retry:
a refusal that arrives on its own is evidence, and one that had to be provoked
is a request we had no reason to make.

WHAT IT WRITES. Metadata and value-free shapes, through the same recorder the
capture tool uses: url, method, resource type, operation, status, and body
shape. No bodies, no headers. Output is 0600 in a gitignored directory.

    walmart browser start          # sign in, clear any press-and-hold
    ./scripts/drive-cart-mutation.py --item-id 33282303 --yes
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pyworker"))

import browser  # noqa: E402
import safety  # noqa: E402

CART_URL = f"{browser.WALMART_ORIGIN}/cart"

# How to find one cart line, in order of preference.
#
# Every one of these is a guess. Walmart's cart DOM has never been recorded,
# and this script refuses rather than improvises when none of them matches
# exactly once -- the difference between a guess that is checked and a guess
# that is acted on. A miss costs nothing: it prints what it looked for and asks
# for the human-driven capture instead.
LINE_SELECTORS = (
    '[data-item-id="{item_id}"]',
    '[data-usitemid="{item_id}"]',
    'a[href*="/ip/{item_id}"]',
)

# Quantity controls within a line, matched on accessible name.
QUANTITY_LABELS = {
    1: ("increase", "add one"),
    -1: ("decrease", "remove one"),
}

# Long enough for a slow reply, short enough that a wedged run ends.
ANSWER_SECONDS = 20.0


class Refused(Exception):
    """Stopped on purpose, before touching anything."""


def capture_module():
    """The recorder, loaded from the capture script rather than copied.

    Two recorders would be two things to keep honest, and the shape rules are
    the part most likely to drift. The file name has a hyphen, so it is loaded
    by path rather than imported.
    """
    path = ROOT / "scripts" / "capture-cart-mutation.py"
    spec = importlib.util.spec_from_file_location("capture_cart_mutation", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("capture_cart_mutation", module)
    spec.loader.exec_module(module)
    return module


def find_line(page, item_id: str):
    """The one element on the page that is this cart line.

    Exactly one. Zero means the cart does not look the way this expects and
    nothing should be clicked. More than one means the script cannot say which
    line it would be changing, which is worse than zero: it would pick the
    first and report a quantity change to whichever line that was.
    """
    tried = []
    for template in LINE_SELECTORS:
        selector = template.format(item_id=item_id)
        tried.append(selector)
        count = page.locator(selector).count()
        if count == 1:
            return page.locator(selector), selector
        if count > 1:
            raise Refused(
                f"{count} elements match {selector!r}. This cannot say which line it would "
                "change, so it changes none of them."
            )
    raise Refused(
        "no cart line found for item "
        f"{item_id}. Tried: {', '.join(tried)}. The cart DOM has never been recorded, so "
        "these are guesses; run scripts/capture-cart-mutation.py and click it yourself."
    )


def find_control(page, line, delta: int):
    """The quantity button for this line, or a refusal.

    Scoped to the line, so a page with ten steppers on it cannot offer the
    wrong one, and checked against the click gate by accessible name before it
    is returned. The gate is what stops this from ever clicking something that
    reads like an order flow, however the page is laid out and whatever
    Walmart renames things to.
    """
    labels = QUANTITY_LABELS[delta]
    for label in labels:
        control = line.get_by_role("button", name=label)
        count = control.count()
        if count == 1:
            name = control.get_attribute("aria-label") or label
            # Belt and braces: the name came off the page, so it is not ours
            # to trust. A "Decrease quantity" button that Walmart has since
            # relabelled into an order flow is not a quantity button, and this
            # is the gate that notices.
            safety.assert_clickable(name)
            return control, name
        if count > 1:
            raise Refused(f"{count} buttons in this line answer to {label!r}; refusing to pick one")
    raise Refused(
        f"no quantity control found in the line for delta {delta:+d}. Looked for buttons named: "
        f"{', '.join(labels)}."
    )


def click_once(page, item_id: str, delta: int) -> str:
    """One click, on one control, in one line.

    Returns the accessible name of what was clicked, so the run can record what
    it did rather than what it meant to do.
    """
    if delta not in QUANTITY_LABELS:
        raise Refused(f"delta must be +1 or -1, not {delta}")

    line, selector = find_line(page, item_id)
    control, name = find_control(page, line, delta)
    print(f"clicking {name!r} in {selector}")
    control.click()
    return name


def drive(page, recorder, item_id: str, delta: int, settle) -> list[str]:
    """Change the quantity, wait for the answer, change it back.

    The restore is not politeness, it is the point: a capture that leaves the
    cart different from how it found it is a tool nobody will run twice. It
    happens in a finally, because the interesting failure is the one that
    happens after the first click.

    Returns notes describing what actually happened, including the failures.
    """
    notes = []
    clicked = None
    try:
        clicked = click_once(page, item_id, delta)
        notes.append(f"clicked {clicked!r} ({delta:+d}) on item {item_id}")
        outstanding = settle(page, recorder, seconds=ANSWER_SECONDS)
        if outstanding:
            notes.append(f"{outstanding} request(s) unanswered after {ANSWER_SECONDS:g}s")
    finally:
        if clicked:
            notes.append(restore(page, recorder, item_id, delta, settle))
    return notes


def restore(page, recorder, item_id: str, delta: int, settle) -> str:
    """Put the quantity back.

    Its own failure is a note rather than an exception: by the time this runs
    the cart has already been changed, and losing that fact to a traceback
    would leave a changed cart and no record saying so.
    """
    try:
        name = click_once(page, item_id, -delta)
        settle(page, recorder, seconds=ANSWER_SECONDS)
        return f"restored with {name!r} ({-delta:+d})"
    except Exception as exc:  # noqa: BLE001 - the cart is already changed; say so
        return (
            f"RESTORE FAILED ({type(exc).__name__}: {exc}). The cart is still {delta:+d} on item "
            f"{item_id}. Fix it by hand."
        )


CONSENT = """This will change your cart.

It clicks one quantity control on item {item_id} ({delta:+d}), records the shape
of Walmart's answer, and clicks it back. Two real requests against your real
cart. Nothing is bought and nothing here goes near an order flow or a payment
-- but the cart is written to, twice.

Type yes to continue: """


def confirm(item_id: str, delta: int, read_line=None) -> bool:
    """Ask before mutating.

    A prompt is a promise that the answer matters, so this one is not a
    y/n: it wants the word. --yes skips it, which is what makes the tool
    scriptable and also what makes the flag the thing to think about.
    """
    # Resolved here rather than in the signature: a default argument is
    # evaluated once, at definition, so `read_line=input` pins this to the
    # builtin as it was then. The first test that replaced input found it had
    # replaced nothing and sat waiting on a real terminal -- the same
    # stale-binding shape as a promise outliving its mechanism, in code this
    # time instead of prose.
    read_line = read_line or input
    return read_line(CONSENT.format(item_id=item_id, delta=delta)).strip().lower() == "yes"


def run(args) -> int:
    capture = capture_module()
    if not args.yes and not confirm(args.item_id, args.delta):
        print("declined; nothing was clicked", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else None
    recorder = capture.Recorder(out)
    notes: list[str] = [f"driven: item {args.item_id}, delta {args.delta:+d}"]
    status = 0
    watched = False

    try:
        options = browser.BrowserOptions(
            profile_dir=args.profile,
            profile_lock=str(Path(args.profile) / ".lock"),
            headless=False,
            cdp_url=args.cdp_url,
        )
        with browser.open_session(options) as session:
            session.on_worker(recorder.on_worker)
            session.on_request(recorder.on_request)
            session.on_response(recorder.on_response)
            watched = True
            # Gated even though it is a constant: the guard belongs on the
            # path that acts, not on the one that asks.
            session.page.goto(safety.assert_navigable(CART_URL), wait_until="domcontentloaded")
            notes.extend(drive(session.page, recorder, args.item_id, args.delta, capture.settle))
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        notes.append(f"refused: {exc}")
        status = 2
    except safety.UnsafeNavigation as exc:
        print(f"refused: {exc}", file=sys.stderr)
        notes.append(f"refused: {exc}")
        status = 2
    except browser.BrowserUnavailable as exc:
        print(f"browser unavailable: {exc}", file=sys.stderr)
        notes.append(f"browser unavailable: {exc}")
        status = 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        notes.append("interrupted")
        status = 2
    finally:
        if watched:
            notes.append(
                f"recorded {recorder.mutations} mutating requests and {len(recorder.responses)} responses"
            )
        path = recorder.write(notes)
        print(f"\nwrote {path}")
        problem = capture.verdict(
            watched,
            recorder.mutations,
            len(recorder.responses),
            readable=sum(1 for r in recorder.responses if r["body_shape"] not in capture.SENTINELS),
            pending=recorder.pending,
        )
        if problem:
            print(problem, file=sys.stderr)
            status = status or 1

    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item-id", required=True, help="the usItemId of a line already in the cart")
    parser.add_argument(
        "--delta",
        type=int,
        default=-1,
        choices=(1, -1),
        help="one step up or down; defaults to down, which is the safer direction",
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--profile", default=str(Path.home() / ".local/share/walmart/attached-chrome"))
    parser.add_argument("--out")
    parser.add_argument("--yes", action="store_true")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
