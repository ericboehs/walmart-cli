# Cross-boundary contracts

Shapes that Ruby produces and Python consumes, written down once so neither side
has to imagine the other.

These exist because imagining went wrong. The cart mutation's store guard was
tested with `pinned_store` as a bare string, while `Walmart::Worker` sends the
whole store object. Both sides passed their own tests and the guard would have
refused every correctly configured request in production, because it compared a
live store id against the rendering of a Hash.

The rule: the Ruby test asserts that the real producer still emits this shape,
and the Python test consumes this file rather than a hand-written literal.
Neither side may edit it alone -- a change here fails the other side's suite.
