"""The search action: build the request, issue it in-session, read the answer.

There are two ways in, and the default is the boring one: the search page is
server-rendered and ships its whole result set inside the HTML, so one ordinary
navigation answers the question. That path needs no persisted hash, sends no
synthesized headers, and looks exactly like a person searching -- which matters,
because Walmart throttles requests carrying only part of the header set the real
page sends.

The second way, kept as a fallback behind transport="graphql", was recovered by
read-only observation of a signed-in session (Safari, 2026-08-26). Walmart's
search is there a *persisted* GraphQL query:
the operation lives in the URL path together with a content hash, and the only
query parameter is a URL-encoded JSON ``variables`` object.

    GET /orchestra/snb/graphql/Search/<hash>/search?variables=<json>

Three rules govern this module, and all three come from things that have
already gone wrong or nearly did:

1. Observed values are copied, not improved. The flag set below is exactly what
   the web app sends. enableSellerType is false and that is *why* sellerType was
   null in the response our parser was built against; flipping it to "get more
   data" would change the response shape out from under search_parse.
2. Nothing is synthesized. Store scoping and device identity belong to the live
   session, so the request is issued from inside the authenticated page rather
   than reconstructed from outside.
3. The persisted hash is version-pinned config, not a law of nature. When
   Walmart rotates it this stops working, and the failure says so instead of
   looking like a parser bug.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode

import browser
import challenge
import protocol
import search_parse

# Pinned to the client build observed on 2026-08-26. Walmart rotates these when
# they ship a new web bundle; STALE_CONTRACT below is what the user sees then.
SEARCH_PERSISTED_HASH = "9df8feef8b7957098efc9cb1432e5de2143a14ab32d5b1df12402ddb712393b5"
SEARCH_PATH = "/orchestra/snb/graphql/Search/{hash}/search"

STALE_CONTRACT = (
    "Walmart's search query hash appears to have changed, which happens when they ship a new "
    "web build. Re-observe it with `scripts/recon.py --live search` and update "
    "SEARCH_PERSISTED_HASH in pyworker/search.py."
)

NOT_SIGNED_IN = "no signed-in Walmart session; run `walmart login` first"

# Walmart throttles synthesized requests that carry only part of the header set
# the real page sends. That is a throttle, not a bot challenge, and conflating
# the two would tell a user to go clear a puzzle that does not exist.
RATE_LIMITED = (
    "Walmart is rate-limiting this session. Wait a few minutes and try again; "
    "repeating the request immediately makes it worse."
)

# Stable operation headers only. Correlation ids, trace ids, and above all
# DEVICE_PROFILE_REF_ID are deliberately absent: the page supplies its own, and
# a device identifier we invented would be both a lie and a fingerprint we chose
# to carry around.
REQUEST_HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "X-APOLLO-OPERATION-NAME": "Search",
    "x-o-platform": "rweb",
    "x-o-segment": "oaoh",
    "x-o-bu": "WALMART-US",
    "x-o-mart": "B2C",
    "WM_MP": "true",
}

# Observed true. Everything else the app sends is false, and stays false.
ENABLED_FLAGS = (
    "enableAdditionalSearchDepartmentAnalytics",
    "enableCopyBlock",
    "enableFacetCount",
    "enablePortableFacets",
    "enablePromoData",
    "enableRelatedSearches",
    "enableSlaBadgeV2",
    "enableUnifiedSchema",
    "fetchBadSplit",
    "fetchDac",
    "fetchDataV1",
    "fetchMarquee",
    "fetchSbaTop",
    "fetchSkyline",
)

DISABLED_FLAGS = (
    "enableSellerType",
    "enableUnifiedProductFragment",
    "enableFashionTopNav",
    "enableSponsoredTopNav",
)

PAGE_SIZE = 40
DEFAULT_PAGE = 1
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_SORT = "best_match"


SEARCH_PAGE = f"{browser.WALMART_ORIGIN}/search"

# The server-rendered search page ships the whole result set in its HTML. Reading
# it costs one navigation, needs no persisted hash, and is the same request a
# person makes -- which matters, because synthesized requests with partial header
# sets get throttled.
NEXT_DATA_SCRIPT = """
() => {
  const node = document.getElementById('__NEXT_DATA__');
  if (!node) { return null; }
  try {
    const data = JSON.parse(node.textContent);
    return data?.props?.pageProps?.initialData?.searchResult ?? null;
  } catch (error) {
    return null;
  }
}
"""

NO_SSR_PAYLOAD = (
    "the search page did not ship its usual embedded results; Walmart may have changed the page, "
    "or the session may need `walmart login`"
)


def search_page_url(params: dict[str, Any]) -> str:
    """The URL a person would visit for this search."""
    query = {"q": params["query"]}
    if params.get("page", 1) != 1:
        query["page"] = str(params["page"])
    if params.get("sort") and params["sort"] != DEFAULT_SORT:
        query["sort"] = params["sort"]
    return f"{SEARCH_PAGE}?{urlencode(query)}"


def normalize_params(request: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize what the CLI asked for.

    Runs before anything opens a browser, so a bad page number costs no time
    and no traffic.
    """
    params = request.get("params") or {}
    query = str(params.get("query") or "").strip()
    if not query:
        raise ValueError("search needs a query")

    return {
        "query": query,
        "page": _bounded_int(params.get("page"), DEFAULT_PAGE, minimum=1),
        "limit": _bounded_int(params.get("limit"), DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT),
        "sort": str(params.get("sort") or DEFAULT_SORT),
        # Observed empty in ordinary signed-in traffic; store context lives in
        # cookies, not here. Kept as a parameter so a future observation can
        # supply it without reshaping the request.
        "store_slot_booked": str(params.get("store_slot_booked") or ""),
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


def build_variables(params: dict[str, Any]) -> dict[str, Any]:
    """The variables object, as the web app sends it.

    Scalars appear three times in the real request (top level, searchParams,
    fitmentSearchParams) with the same values. They are built from one source
    here so they cannot drift apart, which is the kind of inconsistency a
    server-side validator notices and a human never does.
    """
    query = params["query"]
    scalars = {
        "query": query,
        "page": params["page"],
        "prg": "desktop",
        "catId": "",
        "facet": "",
        "sort": params.get("sort") or DEFAULT_SORT,
        "rawFacet": "",
        "seoPath": "",
        "ps": PAGE_SIZE,
        "limit": PAGE_SIZE,
        "ptss": "",
        "trsp": "",
        "beShelfId": "",
        "recall_set": "",
        "module_search": "",
        "min_price": "",
        "max_price": "",
    }

    variables: dict[str, Any] = {
        "id": "",
        "dealsId": "",
        "nudgeContext": "",
        **scalars,
        # Store context is not carried here. It travels in the session's own
        # cookies (assortmentStoreId, isoLoc), which is why an empty string is
        # the correct value rather than a missing one: the page sends the key.
        "storeSlotBooked": params.get("store_slot_booked") or "",
        "postProcessingVersion": 2,
        "version": "v2",
        "fungibilityEnabled": False,
        "tenant": "WM_GLASS",
        "pageType": "SearchPage",
        "adsParams": {"fungibilityEnabled": False},
        "additionalQueryParams": {
            "hidden_facet": None,
            "translation": None,
            "isMoreOptionsTileEnabled": True,
            "isGenAiEnabled": True,
            "rootDimension": "",
            "altQuery": "",
            "selectedFilter": "",
            "neuralSearchSeeAll": False,
            "isModuleArrayReq": False,
            "enableGenericItemTileOptions": False,
            "isLMPBrowsePage": False,
        },
        "searchArgs": {"query": query, "cat_id": "", "prg": "desktop", "facet": ""},
        "searchParams": {**scalars, "cat_id": "", "_be_shelf_id": ""},
        "fitmentSearchParams": {**scalars, "cat_id": "", "_be_shelf_id": ""},
        "fitmentFieldParams": {
            "powerSportEnabled": True,
            "dynamicFitmentEnabled": True,
            "extendedAttributesEnabled": True,
            "extendedAttributesV2Enabled": True,
            "fuelTypeEnabled": True,
        },
    }
    variables.update({flag: True for flag in ENABLED_FLAGS})
    variables.update({flag: False for flag in DISABLED_FLAGS})
    variables["fitmentSearchParams"].update({flag: True for flag in ENABLED_FLAGS})
    return variables


def build_request(params: dict[str, Any], persisted_hash: str = SEARCH_PERSISTED_HASH) -> str:
    """The absolute URL for a search, ready to issue in-session."""
    path = SEARCH_PATH.format(hash=persisted_hash)
    encoded = quote(json.dumps(build_variables(params), separators=(",", ":")), safe="")
    return f"{browser.WALMART_ORIGIN}{path}?variables={encoded}"


class NotSignedIn(Exception):
    """No usable session. Carries the code the CLI already reacts to."""

    code = "login_required"


class RateLimited(Exception):
    """Throttled. Distinct from a block, and never retried automatically."""

    code = "rate_limited"


class StaleContract(Exception):
    """Walmart moved the persisted query out from under us."""

    code = "contract_stale"


def interpret_response(payload: Any, status: int | None = None) -> dict[str, Any]:
    """Turn a raw search response into results, or stop if it is a refusal.

    Order matters: a block payload is not a search result with zero items, and
    parsing one would tell a locked-out user "no matches for whole milk".
    """
    challenge.raise_if_blocked(payload, status=status)
    return search_parse.parse_search(payload)


def _decode(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw or "")
    except (TypeError, ValueError):
        return None


def handle_result(result: dict[str, Any]) -> dict[str, Any]:
    """Map one in-page fetch result to parsed items, or to the right error.

    Pure, so every status Walmart can return is testable without a browser.
    """
    status = result.get("status")
    payload = _decode(result.get("body"))

    # PerimeterX first: a block can arrive on any status, and it outranks every
    # other reading of the response.
    challenge.raise_if_blocked(payload, status=status)

    if status in (401, 403):
        raise NotSignedIn(NOT_SIGNED_IN)
    if status == 429:
        raise RateLimited(RATE_LIMITED)
    if status in (404, 400) and not payload:
        raise StaleContract(STALE_CONTRACT)
    if status != 200:
        raise RuntimeError(f"search failed with HTTP {status}")
    if payload is None:
        raise RuntimeError("search returned a response that was not JSON")

    parsed = search_parse.parse_search(payload)
    return parsed


def action_search(request: dict[str, Any]) -> dict[str, Any]:
    """Worker entry point for `walmart search`.

    Reads the server-rendered page by default: one navigation, no synthesized
    GraphQL, no dependency on a persisted hash Walmart rotates, and no extra
    rate-limit surface. The persisted-query transport stays available behind
    `transport: "graphql"` because it is built and tested, and a second way in
    is worth having the day the first one changes.
    """
    params = normalize_params(request)
    options = browser.BrowserOptions.from_request(request.get("options", {}))
    transport = str((request.get("params") or {}).get("transport") or "page")

    with browser.open_session(options) as session:
        protocol.progress(f"searching for {params['query']!r}")
        if transport == "graphql":
            url = build_request(params, persisted_hash=_hash_for(request))
            data = handle_result(session.fetch_same_origin(url, headers=REQUEST_HEADERS))
        else:
            data = search_page(session, params)

    data["query"] = params["query"]
    data["limit"] = params["limit"]
    data["items"] = data["items"][: params["limit"]]
    return data


def search_page(session, params: dict[str, Any]) -> dict[str, Any]:
    """Navigate to the search page and read the results it shipped."""
    url = search_page_url(params)
    response = session.goto(url, wait_until="domcontentloaded")
    status = getattr(response, "status", None) if response is not None else None

    if status == 429:
        raise RateLimited(RATE_LIMITED)

    # Where we landed decides whether this is a block, before anything is read
    # from the document. A block page is still a page, and it parses to nothing.
    challenge.raise_if_blocked_url(getattr(session.page, "url", None))

    payload = session.page.evaluate(NEXT_DATA_SCRIPT)
    if not payload:
        # An empty page is not an empty catalog. Reporting "no results" here
        # would be a confident answer produced by having read nothing.
        raise RuntimeError(NO_SSR_PAYLOAD)

    return search_parse.parse_search(payload)


def _hash_for(request: dict[str, Any]) -> str:
    """Config may pin a different build's hash without a code change."""
    options = request.get("options") or {}
    return str(options.get("search_hash") or SEARCH_PERSISTED_HASH)
