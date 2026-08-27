# Observing the cart DOM: the minimisation contract

Status: **design under review. No tool exists. Nothing has been observed.**

This document is the thing to argue with. It exists because the observation it
describes is the most privacy-dangerous operation this project has proposed,
and the last tool aimed at this problem was written first and reviewed second.

## Why an observation is needed at all

`walmart cart add` and `cart remove` are shut, and precondition 1 in DESIGN.md
("Driving a cart mutation: what must exist first") is an observed cart DOM. The
deleted driver treated a guessed selector matching exactly one element as
licence to click it. Cardinality is not identity. Before any tool can change a
quantity, somebody has to establish, from evidence, which element is a cart
line, which control changes its quantity, what those controls are named, and
where the current quantity is written.

## Why this is the dangerous one

Every other capture in this project records metadata and shapes. A cart DOM is
different in kind: it is a rendering of Eric's groceries, his store, his
totals, and whatever else Walmart chose to put on the page, including things
neither of us has thought to anticipate. A naive `page.content()` would be the
single worst artefact this repository could produce, and it would be produced
by one line.

So the design starts from what must never be written, and only then asks what
can be.

## Never on disk, never on the terminal

- HTML, in whole or in part.
- Any DOM text node. Any `innerText`. Any screenshot.
- `href`, `src`, `id`, `class`, `data-*` **values** of any kind.
- Product names, prices, quantities as numbers, seller names, store names.
- Item ids, offer ids, cart ids, order ids.
- Request or response headers or bodies.

The list is a checklist for review, not the mechanism. The mechanism is that
the observation builds its output from a fixed vocabulary and never from page
content: nothing reaches the writer that was not already one of a small number
of allowed tokens. **Keys are values** -- an attribute name copied from the page
is page content, so attribute names are reported only when they appear in the
allowlist below, and otherwise counted as `<unrecognised>`.

## What the observation is allowed to say

The target is a description someone could implement a selector from, that a
stranger could read without learning anything about Eric.

For the cart line:

- The tag name and ARIA role of the element that carries the item id.
- The **name** of the attribute carrying it, from an allowlist:
  `data-item-id`, `data-usitemid`, `data-testid`, `id`, `href`. Anything else
  is `<unrecognised>` and counted.
- Whether that attribute's value **equals** the item id already known from the
  parsed cart, or merely contains it. A relation, never the id.
- The ancestor chain from that element to the nearest element that also
  contains a quantity control: tag names and roles only, with depth.
- Cardinality: how many elements matched, across the whole page.

For the quantity control:

- Its role, and whether it is inside the line's subtree.
- Its accessible name, **only** if that name exactly matches the safe
  vocabulary: `Increase quantity`, `Decrease quantity`, `Increase`, `Decrease`,
  `Add one`, `Remove one`. Any other name -- product-specific or simply unseen
  -- becomes `<redacted>`, and the observation is **insufficient**. It is not
  widened to accommodate what was found.
- Whether the control is disabled.

For the quantity itself:

- Whether some element in the line's subtree has a text value **equal to** the
  quantity the parser already read for that line. A relation. The number is
  never written, and the number is already known from the parser, so nothing is
  learned by writing it.
- The tag, role and attribute-name path to that element.

## How the item id gets there without being written

The tool reads the cart first, through the existing parser, in memory. That
gives it the item id, quantity, seller and fulfillment context for the line the
human selected. Those values are used **as needles** -- compared against DOM
values to establish relations -- and none of them is persisted or printed. The
output says "the value at this path equalled the known item id", which is
exactly what an implementer needs and nothing a reader can use.

## Read-only by construction, not by intention

- Only `session.goto`, which runs `safety.assert_navigable`. Never `page.goto`.
- No click, press, fill, focus, hover, tap, check, select, type, drag,
  `set_input_files`, `dispatch_event`, or either `evaluate`. The alarm in
  `test/walmart/recon_tools_test.rb` covers all of them; the tool must also be
  built so that the interaction count is assertable, and fakes must prove it is
  zero.
- No POST. No form submission. No challenge handling beyond stopping and
  saying a challenge was seen.
- Our own tab, in an attached browser, over a loopback-validated CDP URL.
- Bounded: a maximum node count, a maximum depth, a maximum attributes per
  node, and a maximum output size. Exceeding a bound is a reported outcome, not
  a truncation that goes unmentioned.

## Output

- 0600, in the gitignored `pyworker/recon/` tree, like every other capture.
- Whole-file canaries: the known item id, quantity, store id, and a synthetic
  product name are asserted absent from the entire serialised file, in values
  **and in key positions**.
- Three outcomes reported separately, because zero evidence is not success:
  **observed** (the line and its control were described within the vocabulary),
  **refused** (a gate stopped it), **inconclusive** (nothing matched, or a
  control's name fell outside the safe vocabulary, or a bound was hit).

## What this does not do

It does not open any gate by itself. It produces evidence for a review. The
acting implementation arrives in a separate commit, with the state machine, the
pre-read, the verification and the reused mutation guards described in
DESIGN.md -- and not in the same commit as this tool.
