"""A closed vocabulary for describing a page without quoting it.

Everything an observation of the cart DOM may say is in this module, and it is
said in words chosen before anyone looked. See docs/DOM-OBSERVATION.md.

Two properties do the work.

The result types are exact. There is no generic document, no arbitrary mapping
and no list at the writer boundary, so a wrong field cannot be smuggled in
beside a right one and an `observed` result cannot be constructed in a state
that would make it a lie.

The leaves are categorical. Not numbers -- tokens. A cardinality of ONE and a
quantity of one are different kinds of thing, and if both were the integer 1
nothing downstream could tell them apart, least of all a test searching output
for a needle.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, fields

# --- the vocabularies -----------------------------------------------------------------


class Tag(enum.Enum):
    """HTML element names we are willing to name.

    A custom element name is identifier-shaped content the page chose, exactly
    like a dynamic JSON key, so it does not pass merely for being in a tag
    position.
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

    Fixed in advance so implementation cannot add one after seeing what live
    output happened to contain. Widening after a look is how a guess becomes a
    finding.
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


class ControlLocation(enum.Enum):
    """Where the control was found, relative to the element carrying the id.

    This was a bool called `inside_line`, and the name was wrong the moment the
    invariant behind it was relaxed. After resolution the control is *always*
    inside the resolved line container -- that is what resolution means -- so
    "inside line: false" invited a later reader to conclude the control was not
    associated with the line at all. The distinction being drawn is narrower
    and it deserves its own words.
    """

    ID_ELEMENT = "id_element"
    ANCESTOR_CONTAINER = "ancestor_container"


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


class Cardinality(enum.Enum):
    """How many, as a category rather than a number.

    The count of matching elements is only ever used to decide unique against
    ambiguous, so the exact number is not needed -- and emitting it would put
    an integer into the output that could coincide with the quantity. ONE and
    a quantity of one must not be the same token.
    """

    ZERO = "zero"
    ONE = "one"
    MANY = "many"
    OVER_BUDGET = "over_budget"

    @classmethod
    def of(cls, count: int, budget: int) -> Cardinality:
        """Constructed by the walker from a real locator count, never by hand.

        The budget is checked too. It is internal, which is exactly the
        argument that would leave it unchecked -- and then a bool or a string
        budget raises whatever Python raises from the comparison, which is not
        one of this module's fixed codes. A layer claiming closed failure
        behaviour does not get to have one uncontrolled exception in it.
        """
        if type(budget) is not int or type(budget) is bool or budget < 1:
            raise Unserialisable(SerialiseError.BAD_BUDGET)
        if type(count) is not int or type(count) is bool or count < 0:
            raise Unserialisable(SerialiseError.BAD_CARDINALITY)
        if count > budget:
            return cls.OVER_BUDGET
        return {0: cls.ZERO, 1: cls.ONE}.get(count, cls.MANY)


class Depth(enum.Enum):
    """Ancestor distance as a bounded token, not an integer.

    The ladder stops at four because the tool has no business walking further
    up someone's cart page than that, and because a bound expressed as a type
    cannot be exceeded by a loop that forgot to check.
    """

    D0 = "d0"
    D1 = "d1"
    D2 = "d2"
    D3 = "d3"
    D4 = "d4"
    OVER_BUDGET = "over_budget"

    @classmethod
    def of(cls, depth: int) -> Depth:
        if type(depth) is not int or depth < 0:
            raise Unserialisable(SerialiseError.BAD_DEPTH)
        return cls.OVER_BUDGET if depth > 4 else cls(f"d{depth}")


# Four, because the chain runs D1..D4 and the ladder stops there. D0 is the
# line element itself, which is reported separately and is not a step in its
# own ancestry.
MAX_ANCESTORS = 4
MAX_OUTPUT_BYTES = 4096


class Outcome(enum.Enum):
    """Three, because zero evidence is not success."""

    OBSERVED = "observed"
    REFUSED = "refused"
    INCONCLUSIVE = "inconclusive"


class Refusal(enum.Enum):
    """Fixed codes. A refusal never quotes what it refused."""

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

    NONE = "none"
    NODES = "node_budget"
    DEPTH = "depth_budget"
    PROBES = "probe_budget"
    BYTES = "output_byte_budget"


class Inconclusive(enum.Enum):
    BAD_BUDGET = "budget_is_not_a_count"
    NO_MATCH = "nothing_matched"
    AMBIGUOUS = "more_than_one_match"
    UNKNOWN_TAG = "tag_outside_vocabulary"
    UNKNOWN_ROLE = "role_outside_vocabulary"
    UNKNOWN_NAME = "control_name_outside_vocabulary"
    BOUND_HIT = "budget_exhausted"
    DOM_UNREADABLE = "dom_unreadable"


class SerialiseError(enum.Enum):
    """Why the writer refused. A closed set, so the exception cannot carry text.

    The constructor takes one of these and nothing else. An exception whose
    message is a free string is an egress with a public arbitrary-string
    constructor, and this one is raised exactly when something unanticipated
    has arrived -- which is to say when nobody has written a canary for it.
    """

    UNKNOWN_TYPE = "unknown_type"
    WRONG_FIELD_TYPE = "wrong_field_type"
    TOO_MANY_ANCESTORS = "too_many_ancestors"
    OVER_BYTE_BUDGET = "over_byte_budget"
    INCOHERENT_OBSERVATION = "observed_result_is_not_fully_established"
    INCOHERENT_INCONCLUSIVE = "inconclusive_result_disagrees_with_its_limit"
    INCOHERENT_LOCATION = "control_location_disagrees_with_the_ancestor_path"
    BAD_CARDINALITY = "cardinality_not_a_count"
    BAD_BUDGET = "budget_not_a_positive_int"
    BROKEN_CHAIN = "ancestors_are_not_a_contiguous_path"
    BAD_DEPTH = "depth_not_a_count"


class Unserialisable(Exception):
    """Refused by the writer. Carries a code, never the offending value."""

    def __init__(self, code: SerialiseError):
        if type(code) is not SerialiseError:
            raise TypeError("a serialisation error is a SerialiseError")
        super().__init__(code.value)
        self.code = code


# --- the findings ---------------------------------------------------------------------
#
# Each is frozen, and each checks its own field types on construction. The
# check is `type(x) is T`, not isinstance: a subclass of str is a str, and the
# whole point is that no str gets in.


def _typed(value, *allowed):
    if type(value) not in allowed:
        raise Unserialisable(SerialiseError.WRONG_FIELD_TYPE)
    return value


@dataclass(frozen=True)
class AncestorFinding:
    tag: Tag | Sentinel
    role: Role | Sentinel
    depth: Depth

    def __post_init__(self):
        _typed(self.tag, Tag, Sentinel)
        _typed(self.role, Role, Sentinel)
        _typed(self.depth, Depth)


@dataclass(frozen=True)
class LineFinding:
    """The element that carried the item id, and how it carried it."""

    tag: Tag | Sentinel
    role: Role | Sentinel
    attribute: Attribute | Sentinel
    relation: Relation
    matches: Cardinality
    ancestors: tuple[AncestorFinding, ...]

    def __post_init__(self):
        _typed(self.tag, Tag, Sentinel)
        _typed(self.role, Role, Sentinel)
        _typed(self.attribute, Attribute, Sentinel)
        _typed(self.relation, Relation)
        _typed(self.matches, Cardinality)
        _typed(self.ancestors, tuple)
        if len(self.ancestors) > MAX_ANCESTORS:
            raise Unserialisable(SerialiseError.TOO_MANY_ANCESTORS)
        for ancestor in self.ancestors:
            _typed(ancestor, AncestorFinding)


@dataclass(frozen=True)
class ControlFinding:
    """A quantity control, and whether it could be used right now.

    `location` says which element the control descends from: the one carrying
    the item id, or the container resolved around it. Both are ordinary shapes
    and neither disqualifies an observation. The two are not free of each
    other -- see `ObservedResult`, where the location and the ancestor path
    have to tell the same story.

    `disabled` is reported, not required to be false, and the choice is
    deliberate rather than left over. `observed` here means *identified*: this
    element is the quantity control for this line. It does not mean an acting
    implementation could press it, and it must not be read that way -- a
    decrease button is legitimately disabled at a quantity of one, and that is
    still a correct identification. Whether a control is usable is a question
    for the run that wants to use it, asked at that moment.
    """

    role: Role | Sentinel
    name: ControlName | Sentinel
    location: ControlLocation
    disabled: bool

    def __post_init__(self):
        _typed(self.role, Role, Sentinel)
        _typed(self.name, ControlName, Sentinel)
        _typed(self.location, ControlLocation)
        _typed(self.disabled, bool)


@dataclass(frozen=True)
class QuantityFinding:
    relation: Relation
    depth: Depth

    def __post_init__(self):
        _typed(self.relation, Relation)
        _typed(self.depth, Depth)


# --- the results ----------------------------------------------------------------------
#
# Three variants, and no way to be two of them at once. A refusal cannot carry
# findings, an observation cannot carry a refusal code, and an observation
# cannot be constructed at all unless every relation it claims was established.


@dataclass(frozen=True)
class ObservedResult:
    line: LineFinding
    control: ControlFinding
    quantity: QuantityFinding

    outcome = Outcome.OBSERVED

    def __post_init__(self):
        _typed(self.line, LineFinding)
        _typed(self.control, ControlFinding)
        _typed(self.quantity, QuantityFinding)
        self._require_a_path()
        self._require_agreement()
        established = (
            self.line.matches is Cardinality.ONE
            and self.line.relation in (Relation.EQUALS_ITEM_ID, Relation.CONTAINS_ITEM_ID)
            and self.quantity.relation is Relation.EQUALS_QUANTITY
            # The name is probed on a button, so a button is what may be
            # reported. A link or an image bearing the text "Decrease quantity"
            # is not a quantity control, and being inside the vocabulary is not
            # the same as being the thing the vocabulary describes.
            and self.control.role is Role.BUTTON
            and type(self.line.tag) is Tag
            and type(self.line.role) is Role
            and type(self.line.attribute) is Attribute
            and type(self.control.name) is ControlName
            and self.quantity.depth is not Depth.OVER_BUDGET
        )
        if not established:
            raise Unserialisable(SerialiseError.INCOHERENT_OBSERVATION)

    def _require_agreement(self):
        """The location and the ancestor path must say the same thing.

        The old bool was required to be true, so it carried no information but
        it also could not contradict anything. Relaxing it to "reported, not
        required" made two invalid states representable in the same stroke: a
        control at the id element with a non-empty path, and one in an ancestor
        container with no path at all. Loosening a guarantee is not free -- the
        states it used to exclude have to be excluded by something.

        The path *is* the walk to the container, so an empty path means the
        control was found without climbing.
        """
        climbed = bool(self.line.ancestors)
        elsewhere = self.control.location is ControlLocation.ANCESTOR_CONTAINER
        if climbed != elsewhere:
            raise Unserialisable(SerialiseError.INCOHERENT_LOCATION)

    def _require_a_path(self):
        """The ancestors must be a path, not a bag of elements.

        The document promises a chain from the line to the nearest containing
        element, and a chain has an order. Without this, five findings all at
        D1, or D3 before D2, or a gap, would serialise as though somebody had
        walked the tree. Redacted tags and roles are refused here too: an
        observation made of sentinels is a record of not having seen anything.
        """
        chain = self.line.ancestors
        expected = [Depth.of(step) for step in range(1, len(chain) + 1)]
        walked = [ancestor.depth for ancestor in chain]
        readable = all(type(ancestor.tag) is Tag and type(ancestor.role) is Role for ancestor in chain)
        if walked != expected or not readable:
            raise Unserialisable(SerialiseError.BROKEN_CHAIN)


@dataclass(frozen=True)
class RefusedResult:
    refusal: Refusal

    outcome = Outcome.REFUSED

    def __post_init__(self):
        _typed(self.refusal, Refusal)


@dataclass(frozen=True)
class InconclusiveResult:
    reason: Inconclusive
    limit: Limit = Limit.NONE

    outcome = Outcome.INCONCLUSIVE

    def __post_init__(self):
        _typed(self.reason, Inconclusive)
        _typed(self.limit, Limit)
        hit = self.reason is Inconclusive.BOUND_HIT
        named = self.limit is not Limit.NONE
        if hit != named:
            raise Unserialisable(SerialiseError.INCOHERENT_INCONCLUSIVE)


RESULTS = (ObservedResult, RefusedResult, InconclusiveResult)
FINDINGS = (LineFinding, ControlFinding, QuantityFinding, AncestorFinding)
LEAVES = (
    Tag,
    Role,
    Attribute,
    ControlName,
    ControlLocation,
    Relation,
    Sentinel,
    Cardinality,
    Depth,
    Outcome,
    Refusal,
    Limit,
    Inconclusive,
)


# --- the writer -----------------------------------------------------------------------


def _leaf(value):
    if type(value) is bool:
        return value
    if type(value) in LEAVES:
        return value.value
    raise Unserialisable(SerialiseError.UNKNOWN_TYPE)


def _finding(value):
    if type(value) not in FINDINGS:
        raise Unserialisable(SerialiseError.UNKNOWN_TYPE)
    out = {}
    for field in fields(value):
        actual = getattr(value, field.name)
        if type(actual) is tuple:
            out[field.name] = [_finding(item) for item in actual]
        elif type(actual) in FINDINGS:
            out[field.name] = _finding(actual)
        else:
            out[field.name] = _leaf(actual)
    return out


def serialise(result):
    """Turn one of exactly three result types into JSON-ready primitives.

    Takes no mapping and no list. There is nowhere to put a key nobody
    declared, because the keys are the field names of frozen dataclasses --
    derived, not remembered.

    Recursion is bounded by the types themselves: a LineFinding holds at most
    MAX_ANCESTORS AncestorFindings, and an AncestorFinding holds only leaves.
    A cycle cannot be built out of frozen values that must already exist when
    their parent is constructed.
    """
    if type(result) not in RESULTS:
        raise Unserialisable(SerialiseError.UNKNOWN_TYPE)
    document = {"outcome": result.outcome.value}
    for field in fields(result):
        value = getattr(result, field.name)
        document[field.name] = _finding(value) if type(value) in FINDINGS else _leaf(value)
    return document


def render(result) -> str:
    """Serialise to text, and refuse output that is too large to be a summary.

    The byte cap is checked here, in memory, before anything is written -- a
    cap applied after a write is a cap on the next run.
    """
    text = json.dumps(serialise(result), separators=(",", ":"), sort_keys=True)
    if len(text.encode()) > MAX_OUTPUT_BYTES:
        raise Unserialisable(SerialiseError.OVER_BYTE_BUDGET)
    return text


# --- mapping page words into the vocabulary -------------------------------------------


def tag_or_redacted(name):
    """A tag name from the page, mapped into the vocabulary or redacted.

    Exact `str` only. Anything else is redacted without being touched: calling
    `str()` on a hostile object runs its `__str__`, which is code the page's
    library wrote, and the return value of that is not evidence.
    """
    if type(name) is not str:
        return Sentinel.REDACTED
    try:
        return Tag(name.lower())
    except ValueError:
        return Sentinel.REDACTED


def role_or_redacted(name):
    if type(name) is not str:
        return Sentinel.REDACTED
    try:
        return Role(name.lower())
    except ValueError:
        return Sentinel.REDACTED


def control_name_or_redacted(name):
    """Exact match only -- no lowering, because these are probed verbatim."""
    if type(name) is not str:
        return Sentinel.REDACTED
    try:
        return ControlName(name)
    except ValueError:
        return Sentinel.REDACTED
