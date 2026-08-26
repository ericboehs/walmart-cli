"""Worker entry point: read one request from stdin, emit NDJSON, exit.

Every action this worker can perform is listed in ACTIONS and mirrored by
Walmart::Worker::ACTIONS on the Ruby side. There is deliberately no action that
submits an order, and tests assert that stays true.
"""

from __future__ import annotations

import os
import platform
import sys
import traceback
from collections.abc import Callable
from typing import Any

import browser
import login as login_module
import protocol
import safety


def action_probe(request: dict[str, Any]) -> dict[str, Any]:
    """Local environment report for `walmart doctor`. Contacts nothing."""
    options = request.get("options", {}) or {}
    profile_dir = options.get("profile_dir")
    browsers = browser.find_browsers()
    return {
        "protocol": protocol.PROTOCOL_VERSION,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.machine()}",
        "playwright": browser.playwright_version(),
        "browsers": browsers,
        # Present only when a lookup failed for a reason the user can act on.
        # "playwright is broken" and "no browser installed" need opposite fixes.
        "browser_errors": dict(browser.LOOKUP_ERRORS),
        "profile_exists": bool(profile_dir) and os.path.isdir(profile_dir),
        "actions": sorted(ACTIONS),
    }


def action_not_implemented(name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handler(_request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            f"worker action '{name}' is declared but not implemented yet; "
            "it is blocked on live reconnaissance"
        )

    return handler


ACTIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "probe": action_probe,
    "login": login_module.login,
    "session.status": login_module.session_status,
    "store.show": action_not_implemented("store.show"),
    "search": action_not_implemented("search"),
    "item": action_not_implemented("item"),
    "cart.show": action_not_implemented("cart.show"),
    "cart.add": action_not_implemented("cart.add"),
    "cart.increment": action_not_implemented("cart.increment"),
    "cart.decrement": action_not_implemented("cart.decrement"),
    "cart.remove": action_not_implemented("cart.remove"),
    "my_items.list": action_not_implemented("my_items.list"),
    "order.list": action_not_implemented("order.list"),
    "order.show": action_not_implemented("order.show"),
    "recon.capture": action_not_implemented("recon.capture"),
}

# Actions that can change the shared cart. A crash inside one of these is
# reported as unverified (exit 3), never as a clean failure.
MUTATING_ACTIONS = {"cart.add", "cart.increment", "cart.decrement", "cart.remove"}


class UnknownAction(Exception):
    """The request named an action this worker does not have.

    A dedicated exception, rather than KeyError, so that a KeyError raised
    *inside* a handler is reported as an internal error instead of being
    mistaken for an unknown action.
    """


def assert_action_surface_is_safe() -> None:
    """Refuse to run at all if a forbidden action name ever appears."""
    for name in ACTIONS:
        if safety.violations(name):
            raise safety.UnsafeNavigation(f"forbidden worker action registered: {name}")


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    action = str(request.get("action", ""))
    handler = ACTIONS.get(action)
    if handler is None:
        raise UnknownAction(action)
    return handler(request)


def main(argv: list[str] | None = None) -> int:
    assert_action_surface_is_safe()
    try:
        request = protocol.read_request()
    except Exception as exc:  # noqa: BLE001 - a malformed request is a protocol error
        protocol.error("bad_request", str(exc))
        return 2

    action = str(request.get("action", ""))
    mutating = action in MUTATING_ACTIONS

    try:
        data = dispatch(request)
    except UnknownAction:
        protocol.error("unsupported_action", f"unknown worker action: {action}")
        return 2
    except NotImplementedError as exc:
        protocol.error("not_implemented", str(exc))
        return 2
    except browser.ProfileLocked as exc:
        protocol.error("profile_locked", str(exc))
        return 1
    except browser.BrowserUnavailable as exc:
        protocol.error("browser_unavailable", str(exc))
        return 1
    except login_module.BrowserClosed as exc:
        # Distinct from a drifted selector on purpose: the window died, and no
        # amount of staring at Walmart's markup will explain that.
        protocol.error("browser_closed", str(exc), unverified=mutating)
        return 3 if mutating else 1
    except safety.UnsafeNavigation as exc:
        protocol.error("unsafe", str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001 - last resort; report, never swallow
        print(traceback.format_exc(), file=sys.stderr)
        protocol.error(
            "internal",
            f"{type(exc).__name__}: {exc}",
            unverified=mutating,
        )
        return 3 if mutating else 1

    protocol.result(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
