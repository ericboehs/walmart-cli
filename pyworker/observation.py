"""A closed vocabulary for describing a page without quoting it.

Everything an observation can say is in this module. That is the point: the
writer takes values of these types and nothing else, so page content cannot
reach the output by being forgotten about. There is no filter here and no
redaction, because a filter is a denylist wearing a hat -- an unknown type is
rejected rather than cleaned.

See docs/DOM-OBSERVATION.md. The short version: a cart DOM is a rendering of
someone's groceries, and the only safe way to describe it is in words chosen in
advance.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

# --- the vocabularies -----------------------------------------------------------------


class Tag(enum.Enum):
    """HTML element names we are willing to name.

    A custom element name is identifier-shaped content the page chose, exactly
    like a dynamic JSON key, so it does not get to pass through merely for
    being in a tag position.
    """

    DIV = "div"
    SPAN = "span"
    LI = "li"
    UL = "ul"
    OL = "ol"
    SECTION = "section"
    ARTICLE = "article"
    A = "a"
    BUTTON = "button"
    INPUT = "input"
    LABEL = "label"
    P = "p"
    H2 = "h2"
    H3 = "h3"
    IMG = "img"
    FORM = "form"
    TABLE = "table"
    TR = "tr"
    TD = "td"


class Role(enum.Enum):
    """ARIA roles, for the same reason."""

    BUTTON = "button"
    LINK = "link"
    LISTITEM = "listitem"
    LIST = "list"
    GROUP = "group"
    REGION = "region"
    SPINBUTTON = "spinbutton"
    TEXTBOX = "textbox"
    IMG = "img"
    HEADING = "heading"
    NONE = "none"


class Attribute(enum.Enum):
    """The attribute names version one probes, and no others.

    Fixed in advance so that implementation cannot add one after seeing what
    live output happened to contain. The observation may report which of these
    worked; widening the list after a look is how a guess becomes a finding.
    """

    DATA_ITEM_ID = "data-item-id"
    DATA_USITEMID = "data-usitemid"
    DATA_TESTID = "data-testid"
    ID = "id"
    HREF = "href"


class ControlName(enum.Enum):
    """Accessible names generic enough to be nobody's business.

    Probed by exact match. The tool never asks the page for a name it did not
    expect, because asking is reading the thing it promised not to read.
    """

    INCREASE_QUANTITY = "Increase quantity"
    DECREASE_QUANTITY = "Decrease quantity"
    INCREASE = "Increase"
    DECREASE = "Decrease"
    ADD_ONE = "Add one"
    REMOVE_ONE = "Remove one"


class Relation(enum.Enum):
    """How a value on the page stood to a value we already knew.

    The relation is the finding. The value is not, and never leaves the
    process: we knew it before we looked.
    """

    EQUALS_ITEM_ID = "equals_item_id"
    CONTAINS_ITEM_ID = "contains_item_id"
    EQUALS_QUANTITY = "equals_quantity"
    NO_MATCH = "no_match"


class Sentinel(enum.Enum):
    REDACTED = "<redacted>"
    UNREADABLE = "<unreadable>"
    NOT_PROBED = "<not-probed>"


class Outcome(enum.Enum):
    """Three, because zero evidence is not success."""

    OBSERVED = "observed"
    REFUSED = "refused"
    INCONCLUSIVE = "inconclusive"


class Refusal(enum.Enum):
    """Fixed codes. A refusal never quotes what it refused.

    A message that says what was wrong with the value is an egress with an
    excuse, and it is the one that fires exactly when something arrives that
    nobody wrote a canary for.
    """

    NO_CART = "cart_unreadable"
    NOT_EXACTLY_ONE_LINE = "not_exactly_one_eligible_line"
    ITEM_ID_SHAPE = "item_id_not_digits"
    OFFER_ID_MISSING = "offer_id_missing"
    QUANTITY_SHAPE = "quantity_not_positive_integer"
    NOT_FIRST_PARTY = "seller_not_first_party"
    NOT_DELIVERY = "intent_not_delivery"
    NO_PINNED_STORE = "no_valid_pinned_store"
    STORE_DISAGREEMENT = "cart_store_is_not_the_pinned_store"
    CHALLENGE = "challenge_detected"
    UNSAFE = "unsafe_navigation"
    BROWSER = "browser_unavailable"


class Limit(enum.Enum):
    """Which budget stopped the run. A bound hit is a result, not a truncation."""

    NODES = "node_budget"
    DEPTH = "depth_budget"
    PROBES = "probe_budget"
    BYTES = "output_byte_budget"


# --- bounded numbers ------------------------------------------------------------------


@dataclass(frozen=True)
class Count:
    """A cardinality, constructed only by the walker.

    Wrapped rather than passed as a bare int, and this is not fussiness: the
    quantity is an int needle, so a serialiser that accepts plain integers
    cannot tell a cardinality from Eric's quantity. Making the wrapper the only
    way in removes the question.
    """

    value: int

    def __post_init__(self):
        if type(self.value) is not int or self.value < 0:
            raise TypeError("a count is a non-negative int")


@dataclass(frozen=True)
class Depth:
    """Ancestor distance. Same argument as Count."""

    value: int

    def __post_init__(self):
        if type(self.value) is not int or self.value < 0:
            raise TypeError("a depth is a non-negative int")


Leaf = (
    Tag
    | Role
    | Attribute
    | ControlName
    | Relation
    | Sentinel
    | Outcome
    | Refusal
    | Limit
    | Count
    | Depth
    | bool
)


# --- the writer -----------------------------------------------------------------------

# Keys are fixed here, in code. A key taken from the page is page content: see
# "keys are values" in docs/TESTING.md, learned when a scrubber allowed a
# dynamic JSON key through because it was in a key position.
KEYS = frozenset(
    {
        "outcome",
        "refusal",
        "limit",
        "line",
        "control",
        "quantity",
        "tag",
        "role",
        "attribute",
        "relation",
        "matches",
        "depth",
        "ancestors",
        "name",
        "inside_line",
        "disabled",
        "found",
        "probed",
    }
)


class Unserialisable(Exception):
    """Refused by the writer. Carries a code, never the offending value.

    The exception text is an egress like any other, and this one is raised
    exactly when something unexpected has arrived -- which is to say, when
    nobody has written a canary for whatever it is. Saying `got {value!r}`
    here would be the single most likely leak in the tool.
    """

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def serialise(node):
    """Turn a closed structure into JSON-ready primitives, or refuse.

    Accepts: dicts whose keys are in KEYS, lists, and the Leaf types. Anything
    else -- a str, a bare int, a float, an object, a key nobody declared -- is
    rejected. There is no cleaning step, because cleaning is filtering and a
    filter cannot be the guarantee.

    `bool` is checked before `Count`, and both before anything numeric, since
    bool is an int in Python and a stray True would otherwise serialise as 1.
    """
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key not in KEYS:
                raise Unserialisable("unknown_key")
            out[key] = serialise(value)
        return out
    if isinstance(node, list):
        return [serialise(item) for item in node]
    if isinstance(node, bool):
        return node
    if isinstance(node, (Count, Depth)):
        return node.value
    if isinstance(node, enum.Enum) and type(node) in _ALLOWED_ENUMS:
        return node.value
    raise Unserialisable("unknown_type")


_ALLOWED_ENUMS = frozenset({Tag, Role, Attribute, ControlName, Relation, Sentinel, Outcome, Refusal, Limit})


def tag_or_redacted(name):
    """A tag name from the page, mapped into the vocabulary or redacted.

    Never returns the page's string. An unknown tag makes the observation
    inconclusive; the caller is expected to notice the sentinel rather than
    press on.
    """
    try:
        return Tag(str(name).lower())
    except (ValueError, AttributeError):
        return Sentinel.REDACTED


def role_or_redacted(name):
    try:
        return Role(str(name).lower())
    except (ValueError, AttributeError):
        return Sentinel.REDACTED
