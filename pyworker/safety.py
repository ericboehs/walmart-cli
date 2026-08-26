"""The only Python module permitted to name checkout-related concepts.

Safety invariant 1 of DESIGN.md: walmart-cli has no checkout or place-order
action, and browser automation must never navigate into checkout, payment, slot
reservation, or order submission. Recognizing those URLs requires naming them,
so the vocabulary lives here and nowhere else; the source hygiene test scans
every other file in lib/, pyworker/, bin/, and scripts/ for these terms. The
lists below are mirrored in lib/walmart/safety.rb and a test compares them.

Nothing here performs a purchase. This module exists to refuse one.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

# Substrings that must not appear in any other source file. Compared after
# stripping separators, so place_order / place-order / placeOrder all match.
FORBIDDEN_TERMS = (
    "checkout",
    "placeorder",
    "submitorder",
    "purchaseorder",
    "buynow",
    "onestepcheckout",
    "startcheckout",
    "completeorder",
    "payorder",
)

# URL fragments the navigation guard refuses outright.
#
# The leading separator on every entry is load-bearing, not stylistic. The
# whole URL is percent-decoded before matching, query strings included, so a
# bare term here would start refusing ordinary shopping: "checkout organizer"
# is a real thing to search for, and only the separator keeps that search from
# reading as a destination.
FORBIDDEN_URL_FRAGMENTS = (
    "/checkout",
    "/cart/checkout",
    "/place-order",
    "/placeorder",
    "/buynow",
    "/payment",
    "/wallet",
    "/order-confirmation",
)

# The only hosts this tool may navigate to. Matched against the parsed
# hostname, never as a substring, so www.walmart.com.evil.example is refused.
ALLOWED_HOSTS = ("walmart.com",)

# How many times to unwrap percent-encoding before giving up. A handful covers
# any nesting a real URL contains; the cap is there so a crafted input cannot
# make the guard spin.
MAX_DECODE_PASSES = 5

# Accessible names / text of controls automation must never click.
FORBIDDEN_CONTROL_PATTERNS = (
    r"check\s*out",
    r"place\s+order",
    r"continue\s+to\s+(checkout|payment)",
    r"reserve\s+(a\s+)?(time|slot)",
    r"buy\s+now",
    r"submit\s+order",
    r"pay\s+now",
)

_CONTROL_RE = re.compile("|".join(FORBIDDEN_CONTROL_PATTERNS), re.IGNORECASE)


class UnsafeNavigation(Exception):
    """Raised instead of going somewhere this tool must never go."""


def normalize(text: str) -> str:
    """Lowercase and drop every non-alphanumeric character."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def violations(text: str) -> list[str]:
    """Forbidden terms present in ``text`` (used by the hygiene test)."""
    haystack = normalize(text)
    return [term for term in FORBIDDEN_TERMS if term in haystack]


def decode_url(url: str) -> str:
    """Percent-decode until the string stops changing.

    The browser decodes before it resolves, so a guard that matches the raw
    string is not looking at the destination. /%63heckout and
    /orders/..%2F..%2Fcheckout both arrive at the one place this tool must
    never go, and both slipped past a plain substring test. Decoding is
    repeated because %252f decodes to %2f, and capped because an attacker
    picks the input.
    """
    text = url
    for _ in range(MAX_DECODE_PASSES):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    # Windows-style separators resolve as path separators in a URL too.
    return text.replace("\\", "/")


def is_forbidden_url(url: str | None) -> bool:
    """Whether a URL names somewhere this tool must never go.

    Both the raw and the decoded forms are checked. Over-blocking here costs a
    refused navigation; under-blocking costs an order nobody authorised.
    """
    if not url:
        return False
    for candidate in (url.lower(), decode_url(url).lower()):
        if any(fragment in candidate for fragment in FORBIDDEN_URL_FRAGMENTS):
            return True
    return False


def is_allowed_host(url: str | None) -> bool:
    """Whether a URL points at Walmart.

    Parsed rather than matched, because every interesting way to fake a host is
    a string that contains the right one: userinfo (https://www.walmart.com@evil
    .example) and suffixes (www.walmart.com.evil.example) both read as Walmart
    to a substring test and neither is.
    """
    if not url:
        return False
    try:
        parts = urlsplit(decode_url(url))
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False

    host = (parts.hostname or "").lower().rstrip(".")
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_HOSTS)


def is_forbidden_control(label: str | None) -> bool:
    if not label:
        return False
    return bool(_CONTROL_RE.search(label))


def assert_navigable(url: str) -> str:
    """Gate every navigation in the worker. Raises rather than proceeding.

    Two questions, because they fail differently: is this somewhere we must
    never go, and is it even Walmart. Today every call site builds its URL from
    WALMART_ORIGIN, so the host check refuses nothing -- but that is a property
    of the call sites, and this function is what we point at when we say the
    tool cannot wander.
    """
    if is_forbidden_url(url):
        raise UnsafeNavigation(f"refusing to navigate to a forbidden destination: {url}")
    if not is_allowed_host(url):
        raise UnsafeNavigation(f"refusing to navigate off Walmart: {url}")
    return url


def assert_clickable(label: str | None) -> None:
    """Gate every click made by automation."""
    if is_forbidden_control(label):
        raise UnsafeNavigation(f"refusing to click a forbidden control: {label!r}")
