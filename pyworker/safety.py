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


def is_forbidden_url(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return any(fragment in lowered for fragment in FORBIDDEN_URL_FRAGMENTS)


def is_forbidden_control(label: str | None) -> bool:
    if not label:
        return False
    return bool(_CONTROL_RE.search(label))


def assert_navigable(url: str) -> str:
    """Gate every navigation in the worker. Raises rather than proceeding."""
    if is_forbidden_url(url):
        raise UnsafeNavigation(f"refusing to navigate to a forbidden destination: {url}")
    return url


def assert_clickable(label: str | None) -> None:
    """Gate every click made by automation."""
    if is_forbidden_control(label):
        raise UnsafeNavigation(f"refusing to click a forbidden control: {label!r}")
