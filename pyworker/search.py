"""The search action: build the request, issue it in-session, read the answer.

The request contract was recovered by read-only observation of a signed-in
session (Safari, 2026-08-26). Walmart's search is a *persisted* GraphQL query:
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
from urllib.parse import quote

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
        # Store scoping observed in the live request. It belongs to the session,
        # so it is passed through when a caller has it and left empty otherwise
        # -- never hardcoded, never persisted.
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
    if status in (404, 400) and not payload:
        raise StaleContract(STALE_CONTRACT)
    if status != 200:
        raise RuntimeError(f"search failed with HTTP {status}")
    if payload is None:
        raise RuntimeError("search returned a response that was not JSON")

    parsed = search_parse.parse_search(payload)
    parsed["store_scoped"] = bool(result.get("store_scoped"))
    return parsed


def action_search(request: dict[str, Any]) -> dict[str, Any]:
    """Worker entry point for `walmart search`."""
    params = normalize_params(request)
    options = browser.BrowserOptions.from_request(request.get("options", {}))
    url = build_request(params, persisted_hash=_hash_for(request))

    with browser.open_session(options) as session:
        protocol.progress(f"searching for {params['query']!r}")
        result = session.fetch_same_origin(url, headers=REQUEST_HEADERS)
        result["store_scoped"] = bool(params.get("store_slot_booked"))
        data = handle_result(result)

    data["query"] = params["query"]
    data["limit"] = params["limit"]
    data["items"] = data["items"][: params["limit"]]
    return data


def _hash_for(request: dict[str, Any]) -> str:
    """Config may pin a different build's hash without a code change."""
    options = request.get("options") or {}
    return str(options.get("search_hash") or SEARCH_PERSISTED_HASH)
