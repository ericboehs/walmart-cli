"""Scrubbing for captured pages and responses.

Safety invariant 9: nothing recorded from a live session enters the repository
until it has been through here and past the fixture hygiene test.

The rules are deliberately aggressive. A fixture exists to prove a parser
handles a shape; it never needs a real person's data. When in doubt this
replaces rather than preserves, and the parser tests are written against the
placeholders.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

EMAIL_PLACEHOLDER = "redacted@example.com"
PHONE_PLACEHOLDER = "555-555-5555"
REDACTED = "[REDACTED]"

# Header names dropped wholesale from any capture.
DROP_HEADERS = {
    "cookie",
    "set-cookie",
    "authorization",
    "proxy-authorization",
    "x-apollo-operation-id",
    "x-o-ccm",
    "x-o-bu",
    "x-o-gql-query",
    # A stable device fingerprint id. Not a secret, but a durable identifier
    # for one person's machine, and fixtures must never carry one.
    "device_profile_ref_id",
}

# JSON keys whose values are replaced wherever they appear, at any depth.
SENSITIVE_KEYS = {
    "email",
    "emailaddress",
    "phone",
    "phonenumber",
    "firstname",
    "lastname",
    "name",
    "fullname",
    "addressline1",
    "addressline2",
    "addresslineone",
    "addresslinetwo",
    "street",
    "streetaddress",
    "city",
    "latitude",
    "longitude",
    "lat",
    "lng",
    "cardnumber",
    "lastfour",
    "last4",
    "cvv",
    "token",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "sessionid",
    "customerid",
    "cid",
    "authorization",
    # Postal codes are a person's location to within a few miles. Observed
    # unredacted in 20 typeahead URLs because only *names* like "address" were
    # covered and a bare zip did not look like anything.
    "zip",
    "zipcode",
    "postal",
    "postalcode",
    "postcode",
    # Store ids are location by another name: the store you shop is where you
    # live. The key survives, which is the contract; the value does not.
    "storeid",
    "storeids",
    "fulfillmentstoreid",
    "pickupstoreid",
}

TEXT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.IGNORECASE), EMAIL_PLACEHOLDER),
    (re.compile(r"\b(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b"), PHONE_PLACEHOLDER),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), REDACTED),
    (re.compile(r"\bending\s+in\s+\d{4}\b", re.IGNORECASE), f"ending in {REDACTED}"),
    (
        re.compile(
            r"\b\d{1,6}\s+[A-Z][a-z]+\s+"
            r"(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Blvd|Ct|Court|Way|Hwy)\b"
        ),
        REDACTED,
    ),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE), f"Bearer {REDACTED}"),
    (re.compile(r"\beyJ[A-Za-z0-9._-]{20,}"), REDACTED),  # JWTs
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), REDACTED),
)


def scrub_text(text: str, extra_literals: list[str] | None = None) -> str:
    """Apply every textual rule, then any caller-supplied literal strings.

    ``extra_literals`` is how a user's real name or street is removed without
    that value ever being written into this repository.
    """
    for pattern, replacement in TEXT_RULES:
        text = pattern.sub(replacement, text)
    for literal in extra_literals or []:
        if literal:
            text = text.replace(literal, REDACTED)
    return text


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def scrub_json(value: Any, extra_literals: list[str] | None = None) -> Any:
    """Recursively scrub a decoded JSON structure by key and by value."""
    if isinstance(value, dict):
        scrubbed = {}
        for key, item in value.items():
            if _normalize_key(key) in SENSITIVE_KEYS:
                scrubbed[key] = REDACTED
            else:
                scrubbed[key] = scrub_json(item, extra_literals)
        return scrubbed
    if isinstance(value, list):
        return [scrub_json(item, extra_literals) for item in value]
    if isinstance(value, str):
        return scrub_text(value, extra_literals)
    return value


def scrub_headers(headers: dict[str, str]) -> dict[str, str]:
    """Drop credential-bearing headers; scrub whatever remains."""
    return {name: scrub_text(value) for name, value in headers.items() if name.lower() not in DROP_HEADERS}


# Query parameters whose values are replaced wholesale, by normalized name.
SENSITIVE_QUERY_KEYS = SENSITIVE_KEYS | {
    "auth",
    "authtoken",
    "code",
    "data",
    "jwt",
    "payload",
    "pxvid",
    "signature",
    "sig",
    "state",
    "uid",
    "userid",
}

# Any single query value longer than this is opaque by definition: nobody reads
# a 7,000-character parameter, and we have already seen one carry a signed blob.
MAX_QUERY_VALUE = 64

# Longest string kept inside a structured query value. Bounds the blob without
# discarding the structure around it.
MAX_JSON_LEAF = 96


def scrub_url(url: str | None) -> str:
    """Scrub a URL for recording.

    A URL is not metadata, it is data. The first live capture recorded a
    /shoppable-basket navigation whose query string held a 7,044-character
    signed token, and because the recorder stored URLs verbatim that token
    landed in index.json and in a filename. Bodies and headers were scrubbed;
    the URL walked straight past them.

    Path is preserved because it is the useful part (endpoint, operation, item
    id). Query values are replaced when the name looks sensitive, when the
    value is long enough to be opaque, or when the textual rules recognize it.
    """
    if not url:
        return ""

    split = urlsplit(url)
    if not split.query:
        return scrub_text(url)

    pairs = parse_qsl(split.query, keep_blank_values=True)
    cleaned = [(name, _scrub_query_value(name, value)) for name, value in pairs]
    return scrub_text(urlunsplit(split._replace(query=urlencode(cleaned))))


def _scrub_query_value(name: str, value: str) -> str:
    if _normalize_key(name) in SENSITIVE_QUERY_KEYS:
        return REDACTED

    structured = _scrub_structured(value)
    if structured is not None:
        return structured

    if len(value) > MAX_QUERY_VALUE:
        return f"{REDACTED}-{len(value)}-chars"
    return scrub_text(value)


def _scrub_structured(value: str) -> str | None:
    """Scrub a JSON query value in place, keeping its shape. None if not JSON.

    Length was the wrong test on its own. Walmart's persisted GraphQL calls
    carry a ~4,000-character ``variables`` object holding the query text, the
    page number, sort, and store context -- the entire request contract -- and
    a blanket length rule threw all of it away as if it were the 7,044-character
    signed token that motivated the rule. Both are long; only one is a secret.
    Structure tells them apart: parse it, scrub it by key, bound each leaf.
    """
    stripped = value.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        decoded = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    return json.dumps(_bound_leaves(scrub_json(decoded)), separators=(",", ":"), sort_keys=True)


def _bound_leaves(node: Any) -> Any:
    """Replace over-long strings anywhere in a structure, keeping the structure."""
    if isinstance(node, dict):
        return {key: _bound_leaves(item) for key, item in node.items()}
    if isinstance(node, list):
        return [_bound_leaves(item) for item in node]
    if isinstance(node, str) and len(node) > MAX_JSON_LEAF:
        return f"{REDACTED}-{len(node)}-chars"
    return node


def scrub_document(raw: str, extra_literals: list[str] | None = None) -> str:
    """Scrub a captured body, preserving JSON shape when the body is JSON."""
    stripped = raw.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            return json.dumps(scrub_json(json.loads(raw), extra_literals), indent=2, sort_keys=True)
        except json.JSONDecodeError:
            pass
    return scrub_text(raw, extra_literals)
