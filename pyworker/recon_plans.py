"""Read-only reconnaissance plans.

Each plan is a list of surfaces to visit while recording what the real web app
does. Every URL here must be read-only and must pass the navigation guard; the
tests in test_recon_plans.py enforce both.

These URLs are hypotheses drawn from public knowledge of walmart.com. The whole
point of recon is to correct them, so treat a 404 as data, not as a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ORIGIN = "https://www.walmart.com"


@dataclass(frozen=True)
class Plan:
    name: str
    description: str
    urls: list[str] = field(default_factory=list)
    # What this plan is supposed to answer, from DESIGN.md's recon list.
    questions: list[str] = field(default_factory=list)
    # True when the human is expected to drive the browser while we record.
    interactive: bool = False


PLANS: dict[str, Plan] = {
    "observe": Plan(
        name="observe",
        description=(
            "Open the browser and record everything while the human clicks around. "
            "The most honest way to learn a surface we cannot guess."
        ),
        urls=[ORIGIN],
        questions=[
            "what bot protection appears, and when",
            "which internal endpoints the real app calls",
            "which identifiers are stable across pages",
        ],
        interactive=True,
    ),
    "session": Plan(
        name="session",
        description="Confirm the session survives a fresh headless run.",
        urls=[f"{ORIGIN}/account"],
        questions=["session lifetime and profile reuse", "headless acceptance"],
    ),
    "store": Plan(
        name="store",
        description="Find where the active grocery store and its fulfillment context live.",
        urls=[ORIGIN, f"{ORIGIN}/grocery"],
        questions=[
            "store id, name, and ZIP as the app sees them",
            "how delivery vs pickup fulfillment context is expressed",
        ],
    ),
    "search": Plan(
        name="search",
        description="Capture the search contract and the fields an offer exposes.",
        urls=[f"{ORIGIN}/search?q=whole+milk"],
        questions=[
            "search request contract and pagination",
            "seller and delivery-eligibility fields",
            "item and offer identifiers",
        ],
    ),
    "cart": Plan(
        name="cart",
        description="Capture the cart read contract. Reads only; no mutation in this plan.",
        urls=[f"{ORIGIN}/cart"],
        questions=[
            "cart line identifiers",
            "weighted-item quantity semantics",
            "warnings Walmart attaches to a line",
        ],
    ),
    "my-items": Plan(
        name="my-items",
        description="Find the personalized My Items collection and its grouping.",
        urls=[f"{ORIGIN}/my-items", f"{ORIGIN}/lists"],
        questions=["My Items surface, grouping, and pagination"],
    ),
    "orders": Plan(
        name="orders",
        description="Find online order history and in-store receipts.",
        urls=[f"{ORIGIN}/orders", f"{ORIGIN}/account/orders"],
        questions=[
            "order and receipt history surfaces",
            "pagination limits and how far back history goes",
            "normalized fulfillment types",
        ],
    ),
}


def plan_names() -> list[str]:
    return sorted(PLANS)
