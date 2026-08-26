"""The search action: the halves we can build honestly, and the one we cannot.

Two of the three pieces of `walmart search` are known from observation:

  * what a blocked response looks like (challenge.py), and
  * what a successful response contains (search_parse.py).

The third -- how to *ask* -- is not. Walmart's search is a persisted GraphQL
query whose ``variables`` parameter carries the query text, page, sort, and
store context, and that parameter was destroyed by our own scrubber during the
only capture that contained it. See scrub.py for why, and for the fix.

So the request builder raises instead of guessing. A guessed request is worse
than no request: it fails against the live site in a way that looks like
Walmart changing, and it burns a bot-protection budget we do not control while
teaching us nothing.
"""

from __future__ import annotations

from typing import Any

import challenge
import search_parse

# The exact blocker, phrased for whoever hits it. It names the missing evidence
# and the command that would collect it.
REQUEST_CONTRACT_UNKNOWN = (
    "search cannot run yet: the persisted-query request contract has not been observed. "
    "The `variables` parameter (query, page, sort, store) was redacted out of the only "
    "capture that contained it. Collect it with `scripts/recon.py --live search`, then "
    "implement build_request() in pyworker/search.py."
)

DEFAULT_PAGE = 1
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def normalize_params(request: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize what the CLI asked for.

    Separate from the request builder on purpose: this half is knowable and
    testable today, and it fails on a bad page number without opening a
    browser.
    """
    params = request.get("params") or {}
    query = str(params.get("query") or "").strip()
    if not query:
        raise ValueError("search needs a query")

    return {
        "query": query,
        "page": _bounded_int(params.get("page"), DEFAULT_PAGE, minimum=1),
        "limit": _bounded_int(params.get("limit"), DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT),
    }


def _bounded_int(value: Any, default: int, *, minimum: int, maximum: int | None = None) -> int:
    if value is None or value is True or value is False:
        return default
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected a whole number, got {value!r}") from exc
    if number < minimum or (maximum is not None and number > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f"{minimum} or more"
        raise ValueError(f"{number} is out of range ({bound})")
    return number


def build_request(_params: dict[str, Any]) -> str:
    """Blocked seam: the URL Walmart's search expects.

    Deliberately not implemented. When the variables contract is observed, this
    is the only function that has to change -- parsing and block detection are
    already written and tested against it.
    """
    raise NotImplementedError(REQUEST_CONTRACT_UNKNOWN)


def interpret_response(payload: Any, status: int | None = None) -> dict[str, Any]:
    """Turn a raw search response into results, or stop if it is a block.

    Order matters: a block payload is not a search result with zero items, and
    parsing one would report "no matches for whole milk" to a user who is
    actually locked out.
    """
    challenge.raise_if_blocked(payload, status=status)
    return search_parse.parse_search(payload)


def action_search(request: dict[str, Any]) -> dict[str, Any]:
    """Worker entry point for `walmart search`.

    Validates the request, then stops at the blocked seam. The validation runs
    first so an impossible page number is reported as a usage problem rather
    than hidden behind the bigger blocker.
    """
    normalize_params(request)
    raise NotImplementedError(REQUEST_CONTRACT_UNKNOWN)
