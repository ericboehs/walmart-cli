# Reviewing a proposal to observe a live cart

Status: **this is a review checklist, not a runbook. No runnable observer
exists. Nothing in this document authorises anything, and following it is not
permission to run.**

The offline layer — the closed vocabulary in `pyworker/observation.py` and the
walker in `pyworker/observe_walk.py` — has been reviewed and accepted as
*offline*. Acceptance of that layer is not acceptance of a way to run it. There
is deliberately no entrypoint, and the reasoning for that is recorded below
rather than deferred. (An earlier draft of this paragraph said the decision was
recorded in `docs/DOM-OBSERVATION.md`. It was not; no such statement exists
there. A cross-reference to a sentence nobody wrote is worse than no
cross-reference, because it sends a reader away satisfied.)

This file exists so that the argument for a live observation has to be made
once, in advance, against written criteria — instead of being assembled in the
moment by whoever happens to want the answer. What follows are the gates a
future proposal must satisfy **before any plumbing is reviewed at all**. They
are conditions of review, not steps to perform.

A proposal failing any single gate is refused as a whole. There is no partial
credit and no "the rest is fine": the gates are not a score.

## Why there is no runner

The obvious next artifact is a small offline runner, and it is the wrong one.

A dormant entrypoint is operational reach without evidence. It would sit in the
tree, fully wired, waiting for someone who has not read this thread to supply
the one missing argument — and everything protecting it would be a comment. A
shut door whose key is one source edit is a TODO wearing a lock.

The work a runner would supposedly do is already done. The fakes exercise the
walk end to end, including its most expensive path, its refusals, and its
boundary. A runner would add nothing except the ability to run.

## Gate 1: authorisation

- Fresh, explicit authorisation from the account's owner, given for this
  purpose and not inherited from any earlier permission.
- Naming **the exact commit** to be run. Authorisation attaches to a revision,
  not to a project, because the thing being permitted is a specific set of
  reviewed behaviours.
- Naming **one** observation attempt. Not a session, not a period, not "while
  we are in there".
- Given after the proposal has been reviewed, not alongside it. Approval of a
  plan and approval of a run are separate acts.

A peer agent's agreement is not authorisation, and neither is this document.

A live attempt is worth *proposing* only when someone intends to read the
structural result -- either to find out whether the offline walker matches the
live DOM, or as evidence toward a future acting design. Not because the code
exists. While the response-shape and per-line delivery gates are both shut,
the structure of the page has to be the question being asked, or there is no
question.

## Gate 2: the session

- The signed-in session is established by the person, at the keyboard.
- Any challenge is cleared by the person. No automation of a challenge, no
  detection-evasion, no fingerprint work in service of getting past one.
- A challenge appearing at any point stops the attempt. It does not pause it.
- No automatic retry of anything, for any reason, including a stop that looks
  transient. If an attempt ends, the next one needs Gate 1 again.
- Human-rate traffic throughout. The observation is one read of one page.

## Gate 3: the connection and the page

- Attachment over loopback only. A CDP endpoint that is not on the local
  machine is refused rather than warned about.
- A newly opened tab belonging to this attempt.
- Navigation confined to the cart URL, through the existing navigation safety
  gate. No proposal may introduce a second way to navigate.
- The person's own tabs, browsing context and browser are never touched,
  reused, or closed. The attempt cleans up only what it opened.
- No page-level state is modified: no storage writes, no cookie edits, no
  reloading to "get a better shape".

## Gate 4: the code being proposed

- An exact inventory of every file and function the entrypoint would touch,
  written down in the proposal rather than discoverable from the diff.
- No browser is constructed anywhere outside the existing session type. A
  second way to make a browser is a second set of everything that governs one.
- No interaction method appears on any path reachable from the entrypoint —
  and this is checked by the existing source alarms, not asserted in prose.
- The entrypoint is thin to the point of being uninteresting. Anything it wants
  to decide is a thing that should have been decided offline and tested.

## Gate 5: preconditions, on the live parse

Established from the parsed cart, in memory, **before the first locator
exists** — a precondition checked after the first read is not a precondition:

- exactly one line in the cart, on the strict reading, with no selection among
  candidates;
- item id and store id that are exact ASCII digit strings, unnormalised;
- an offer id present and non-blank;
- a quantity that is a positive integer and not a bool;
- a first-party line, decided by the shared predicate rather than restated;
- a fulfillment intent that is exactly delivery;
- a live store id exactly equal to a valid pinned store id.

## Gate 6: what may leave the process

- The item id, offer id, store id and quantity appear in **no** argv, no
  environment variable, no selector, no filename, no stdout or stderr, no
  exception message, and no debug output.
- The only output is the typed render: exactly one result document, built from
  the closed vocabulary, and nothing alongside it.
- Exit codes as already contracted: 0 observed, 2 refused, 3 inconclusive.
- No free-text diagnostics. A rejection that reports what it rejected is an
  egress with an excuse, and it fires exactly when something arrives that
  nobody wrote a canary for.

## Gate 7: persistence, if any is proposed

Preferably none. The result is small enough to read.

If persistence is proposed anyway:

- the document is rendered in memory and size-checked before anything is
  opened for writing;
- a byte cap enforced before the write, not after;
- an atomic write to a fixed filename, mode 0600, in a gitignored location;
- no ambiguity about overwriting: the behaviour is stated in the proposal and
  is the same on every run.

## Gate 8: budgets and stopping

- The complete read budget and a zero interaction count are demonstrated by the
  tests of the reviewed commit, not by the run.
- The attempt stops — without retrying — on a challenge, an unsafe navigation,
  an unexpected exception, a budget bound being hit, or a result the type layer
  refuses as incoherent.
- Stopping is the ordinary outcome. An attempt that stops has not failed.

## Gate 9: what is permanently excluded

Not "out of scope for this attempt". Excluded:

- any mutation of the cart, any quantity change;
- any observer-authored network call: no direct POST, no request API, no
  GraphQL operation, no fetch or XHR issued by this tool. The one action that
  causes network traffic is the single gated navigation.

  Stated that way on purpose. "No POST" was the earlier wording and it is not
  enforceable as written: loading a cart page causes Walmart's own scripts to
  issue background requests, including POSTs, and a prohibition that the very
  first permitted action violates is one a reader learns to discount. The
  boundary that can actually be held is the one between traffic this tool
  authors and traffic the page generates by being open.
- the order-submission surface, in every spelling, which no proposal may name
  outside the two audited safety files;
- restoring, repairing or tidying the cart afterwards — the observation leaves
  the cart exactly as the person left it;
- opening either readiness gate.

## Gate 10: afterwards

- Only the typed structural artifact is reviewed. Screenshots, page text and
  recollections of what the page looked like are not evidence and are not
  collected.
- **A successful observation does not by itself authorize or activate acting
  code.** After separate human review, its typed structural evidence may
  establish part of the observed DOM/control contract and inform a later
  proposal for an acting state machine. It cannot substitute for
  successful-response semantics, per-line delivery evidence, executing-boundary
  checks, or review of the acting implementation itself.

  The earlier wording here was "a successful observation does not enable acting
  code", flat. That is the same category error this project has already
  retracted once -- in the retracted claim that a read cannot be evidence about
  a write -- and it takes a true narrow limit -- this artifact authorizes nothing on its own -- and
  inflates it into a false general one. Structural evidence is exactly what
  this observer exists to produce, and saying it counts for nothing would mean
  the observer had no purpose. Overstating a boundary does not make it stronger;
  it makes it arguable, and a boundary that can be argued away is one that will
  be, by someone who has correctly noticed the overstatement.
- `PER_LINE_DELIVERY_PROVEN` stays shut. This observer collects no per-line
  fulfillment signal, so it cannot open it — a different, separately reviewed
  observation might contribute, and would need its own proposal.
- The status lines and their parity tests change in **the same commit** that
  introduces plumbing, never before it. A document that describes a capability
  the tree does not have yet is the promise-outliving-its-mechanism pattern
  running backwards, and it is the single most-recorded defect in this project.
