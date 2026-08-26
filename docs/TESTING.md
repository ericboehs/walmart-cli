# Testing this repository

`rake` runs everything: rubocop, ruff, minitest, pytest, and `script/smoke`.

The layout of the suite is not arbitrary. It is the residue of eighteen defects
found in two days, eleven of which were sitting inside checks that were passing
at the time — including one found while writing the fix for another, in this
file's own audit. This file records what those had in common, because the mistakes
were not careless ones and will be made again by anyone who only reads the code.

## The failure mode: a check that cannot see what it is named after

Each of these was written deliberately, read fine, and passed:

| The check | What it could not see |
| --- | --- |
| an ordering test with one element | every comparator sorts a one-element list correctly |
| `File.exist?` for a stale browser lock | Chromium writes a symlink to a `host:pid` target that does not exist, so it follows the link and answers no |
| a write-then-`chmod` archive test | the window where the file is world-readable |
| smoke's `--json` cases | they ran after `order sync`, so none of them was empty — the empty-document bug passed |
| smoke's credential canary | it scanned for a password in a run where none existed |
| smoke's worker-failure cases | the stub spoke the wrong protocol, so they hit a transport error that shares an exit code with the failure they named |
| every test of `item` and `cart show` | they asserted on stdout and discarded the return value, while both commands returned `nil` and crashed at `Kernel#exit` |
| the redaction tests | they pinned the *mechanism* — depth, lists, spelling — and nothing pinned the *membership*, so deleting `paymentMethods` from the denylist left the whole suite green while card details survived into the archive |
| smoke's only stopped-sync case | it used the reason `page_changed`, which no worker can emit, so the one end-to-end stop test exercised the catch-all branch and said nothing about `page_limit` or `no_next_page` — the two most likely real stops |
| `SETTLED`, the list deciding whether a sync reports success | nothing named its contents, so adding a real stop reason to it made a truncated sync exit 0 with all 405 checks green |
| `order list` on an empty archive | it assumed empty meant never-synced, so after a sync Walmart blocked before it read anything, the tool said "nothing is archived yet — run sync first". Sync had run; re-running unattended is what drew the block |
| the shared `positive_integer_flag` message | it hardcoded "seconds", true of its only caller when written and wrong for the four that followed, so `order list --limit 0` asked for a whole number of seconds. No test asserted any wording |
| `test_unpinned_store_warns_with_the_next_step` | it required the hint to say `walmart store pin`, a command the CLI does not implement — the check held the defect in place instead of catching it, and its name reads as entirely reasonable |
| `doctor`'s credential check | it probed for an item's *presence* and reported "email + password set", while the password item existed and held nothing; `login` read the value, found none, and offered to create the record that already existed |
| the first draft of the per-command help table | hand-written from plausibility rather than from `parse_flags`: it invented two flags that do not exist and omitted five that do |
| the first draft of the page-ceiling test | it asked for 10,000 pages and got 200, which is equally satisfied by ignoring the parameter — which is what the code did, because the test passed the wrong argument shape |

The `item` and `cart show` one shipped, in two commands, on every JSON
invocation.

The last is a distinct sub-shape worth recognising on sight: the check tests a
mechanism correctly, while the thing it protects is **configuration the
mechanism reads**. A list, a table, a set of patterns. The mechanism has tests
and the data has none, so a rename, a merge resolution, or a lint autofix
removing an unfamiliar-looking entry all pass. Expect it again the moment a
second denylist appears.

The clearest evidence that this is a shape rather than a lapse: the Ruby guard
written to fix that leak *had the same bug in it*. Two passing tests exercising
two fields, and deleting a third from the list kept everything green — written
twenty minutes after fixing the identical defect in Python, in a file that was
still open, with the problem fully in mind. It did not transfer, because the
defect is not carried by the code or by the author's attention. It is carried by
the situation: a list, and tests that exercise the mechanism without ever naming
the list's contents.

Recognising the shape transfers. Remembering the instance does not. Which is why
the rule has to fire without recall — mutate a new list *because it is a new
list*, not because you remember why.

Two tests are needed there, not one, because they fail differently. One asserts
the list *is* the documented set, so shrinking it fails by name. The other pushes
a payload carrying every entry through the real code path, so it survives
somebody replacing the mechanism entirely. The first alone dies in a refactor;
the second alone cannot tell you *which* field stopped being protected.

None of these is an attention failure. Assertions get written from inside the
code, and from there the return value of a print, the exit status of a process,
and the difference between two error codes are all invisible. They are the first
things the user meets.

## The one to read first

The last defect found was the first draft of a test whose entire purpose was
catching this failure mode. It asserted that a request for 10,000 pages came
back clamped to 200, and it passed. But "asked for 10,000, got 200" is equally
satisfied by *ignoring the parameter*, and that is what the code did, because
the test handed the function the wrong argument shape. Green, for the wrong
reason, in a test written specifically to catch things that are green for the
wrong reason, an hour after this file was updated to say the defect is carried
by the situation rather than by the author's attention.

Nothing was going to fix that by being careful. What caught it was writing the
other half — "may still ask for fewer pages" — because a clamp has two sides and
asserting only one cannot distinguish a clamp from a constant. That is the
generalisation worth taking:

**A property with an inverse must be asserted from both sides.** One direction
alone cannot tell a clamp from a constant, a sort from a coincidence, or an exit
status of 3 from an exit status that is always 3. This needs no adversary and no
imagination, which is why it works when mutation testing is not on your mind.

## The one bug under four of the others

**A mechanism that locates its subject by pattern must assert it found one.**

Four defects tonight were the same bug wearing different clothes:

| The search | What returned nothing |
| --- | --- |
| `File.exist?` on a browser lock | Chromium's `SingletonLock` is a symlink to a `host:pid` target that does not exist, so the check followed it and answered "no lock" |
| a smoke case matching a stop reason | `page_changed` is emitted by nothing, so the case matched the catch-all branch and proved nothing |
| a mutant's search-and-replace | the pattern did not match, the file did not change, and the suite passed — reported as a guard that works |
| a test grepping the README by heading | rename the heading and it looks for something absent, then checks nothing |

In every one, **a search that returned nothing was read as a search that found
nothing wrong.** Absence and success are indistinguishable at the call site
unless something insists the subject exists first. So: assert the lock path
resolves, assert the reason is one the worker can emit, refuse to run when the
mutation did not apply, assert the block was found before asserting on its
contents.

This is the rule to reach for when a check is about *finding* something. It is
also the one that keeps recurring after being written down, which is the whole
argument of this file.

## The two habits that actually found them

**1. Run the real command and read all three channels.** Not the tests — the
bytes. stdout, stderr, and the exit status. `script/smoke` exists to make this a
build step rather than something to remember; it drives the real binary with
`WALMART_PYTHON` pointed at a stub that speaks the worker protocol, so
everything but Walmart is genuine.

**2. Make the failure exist, then confirm the check fires.** A check that has
never failed is a check you are assuming works. Then the second half, which is
the part that gets skipped: **hand it a *different* failure and confirm it
notices**. A check that fires for the right reason and one that fires for any
reason are indistinguishable from a single green run.

The second half requires acting against a green suite with nothing prompting
you, which is exactly why seven defects were sitting in checks that passed. Two
practices make it survive: record the mutants and their failures in the commit
message, and prefer assertions that name the specific thing — an error code, not
"some JSON came out"; an exit status, not "it printed something".

**The plan is an inventory of silent failures, so it rots silently too.** Rename
a constant and the mutant proving its guard works stops applying to anything;
`rake mutants` would say so, but only if someone runs it, and the reason the plan
exists is that nobody is watching these guards. `test/walmart/mutation_plan_test.rb`
therefore checks the plan the way the plan checks the guards — every file still
present, every pattern matching exactly one place, no mutant a no-op, every
Python mutant checked by a Python suite. That is the half a machine can do.

The other half cannot be automated and is a habit instead: **when you add a
guard, add a mutant.** Nothing fails if you don't — that is the definition of
the guards in this file — so the moment to do it is while writing the guard,
when you still know what it is protecting against. Cart add will need entries
for the seller check at mutation time, the quantity arithmetic, and the
confirmation gate.

**A mutation run must prove the mutation happened before it proves anything
else.** Twice in one night an edit matched nothing, changed nothing, reported
nothing, and the suite passed — which is indistinguishable from a mutant that
survived, and means the opposite. `script/mutate` refuses to run the suite
unless the file actually changed, and refuses an ambiguous pattern rather than
guessing which occurrence was meant. `script/mutants.json` holds the standing
set; `rake mutants` runs it.

## A test that prevents, not one that verifies

Sixteen defects, and exactly one check fired *before* the mistake was committed:
the help-table completeness test, which named four commands missing from the
table while the table was still being written. Everything else here catches a
defect after it exists.

The difference is not effort, it is what the check compares against. A test that
compares behaviour to a fixture we wrote can only confirm we did what we meant.
A test that compares one artifact to **a source of truth that already exists** —
`parse_flags` for the help table, `orders.py` for the stop reasons,
`docs/CONTRACTS.md` for the denylist, `CLI::COMMANDS` for coverage — fails while
you are still typing, because the other side was there first and did not move.

Prefer that shape when it is available. It is rarer than it sounds and worth
more than another assertion on behaviour.

## Rules with a reason

- **A guard and the test that hunts what the guard catches must not share a code
  path.** `CLI.run` repairs a non-integer return into `FAILED`, so
  `exit_code_contract_test.rb` calls `CLI#run` instead. Routed through the
  repair, the test would receive a tidy integer and pass over the live bug.
- **A guard with no caller is indistinguishable from a guard.** Removing the
  `exit_status` call from `CLI.run` failed nothing until a wiring test existed.
- **Coverage lists need a completeness test.** Fifteen invocations prove
  something about fifteen commands and nothing about the sixteenth, and the
  sixteenth is the one written in a hurry. `exit_code_contract_test.rb` walks
  `CLI::COMMANDS` and fails by name when an entry is missing.
- **Any ordering claim needs at least three elements with deliberately
  adversarial keys.** Two cannot distinguish a key sort from a reversal, and
  correlated keys cannot distinguish one key from another.
- **A check may only claim what it looked at.** `doctor` never reads a
  credential value, deliberately, because that can prompt for biometrics -- and
  it reported "set". Presence is not usability: the item existed and was empty.
  Three components each behaved correctly and the user got "set" from one and
  "no stored credentials found" from another. The wording now stops where the
  evidence stops: "present (values not read)".
- **A suggestion is a promise about the tool.** After signing in, the CLI said
  "next: walmart store pin", which it answers with "unknown command" — the first
  instruction a new user is given, and it did not work. `PLANNED` exists so
  `--help` can name unbuilt commands honestly, which made the vocabulary
  available everywhere while nothing said which contexts may use it. Help may
  name planned commands; nothing else may, and
  `test/walmart/suggested_commands_test.rb` enforces that against `CLI::PLANNED`.
- **A test can hold a defect in place as firmly as it can catch one.** The store
  hint was wrong for as long as it existed, and a test named
  "warns with the next step" required it to stay wrong. When fixing a defect,
  find the test that was asserting the old behaviour — if there isn't one, ask
  why not; if there is, it was written by someone who also did not check.
- **A safety property contradicted in the prose is not enforced.** The code
  never retries a bot challenge automatically — and the sentence printed to the
  user said "run `walmart order sync`", which is the same retry, by hand, at the
  moment it is most likely to escalate a soft challenge into a hard block. The
  prose is the interface. A rule that lives only in the code protects only the
  paths that do not ask a human to act.
- **Documentation is code that cannot fail, so give it a parity test.** The help
  table was written from what the flags plausibly were, and it both invented
  flags and omitted them — a flag the tool documents and then rejects is worse
  than an undocumented one. `test/walmart/help_test.rb` compares the table
  against what `parse_flags` accepts, per file, in both directions.
- **A name is a claim; the mechanism under it is a separate fact.** `MAX_PAGES`
  said ceiling and the code said default — `_bounded(request_value, MAX_PAGES)`
  honoured any number the caller named. Both read as agreeing until you ask who
  supplies the value. Nothing was wrong in practice only because our own CLI
  never sends the field, which is a fact about today's caller and not about the
  worker; the worker is a separate process precisely so it need not trust its
  caller. A limit the caller can raise is not a limit.
- **A constant is a list of one, and the question is the same.** `MAX_PAGES`
  lowered stops the walk early with a resumable archive; raised, it is an
  unbounded loop against someone else's site, unattended. Pinned. Applying the
  rule to constants also found that the ceiling was only a default — the request
  could name any number and get it — which no caller exercised and no test could
  have caught, because there was nothing to catch.
- **Two checks can both be sound and still leave the gap between them.** The
  stop-reason classification was pinned in Ruby and the exit code was proven end
  to end, and neither could see the other's blind spot: the only end-to-end stop
  used a reason that cannot happen. Ask what a pair of checks *jointly* fails to
  cover, not whether each is correct.
- **Never derive a test's inputs from the thing under test.** The check that
  every unsettled reason exits non-zero first read `KNOWN_REASONS - SETTLED`, so
  widening `SETTLED` shrank the list being checked and the test stopped looking
  at the reason that had just become wrong. It is written out as a literal now,
  with a completeness test tying the two literals back together.
- **Ask which direction a list fails in before deciding it needs pinning.**
  Shrinking `DROP_KEYS` or growing `WALMART_SELLERS` fails *open* and silently,
  so both are pinned to a documented set. Shrinking `WALMART_SELLERS`, or losing
  a `TYPE_LABELS` entry, or missing a `NEXT_PAGE_LABELS` string all fail
  *closed* into behaviour that is documented and visible — an item refused, a
  label kept verbatim, a walk that stops with a reason and a resumable archive.
  Those are deliberately not pinned; a membership test there is churn that
  fights ordinary maintenance.
- **A failure message on a list is a fork in the road, so write it as one.** The
  exact-set test names `docs/CONTRACTS.md` as the authority and says not to edit
  the list to satisfy the test, because "make the parity failure go away" is the
  natural next move and it leads straight back to the leak.
- **Two independent copies beat one shared constant, when the failure being
  survived is the copy itself being wrong.** `Redact::FORBIDDEN_KEYS` duplicates
  the worker's `DROP_KEYS` on purpose: the worker removes those fields, and the
  archive refuses any record still carrying one. A shared constant would fail
  both at once. A parity test compares them as text, so separate does not become
  divergent.
- **Negative controls for absence checks.** Smoke plants a real credential via a
  fake `security` on `PATH` and requires one case to *find* it, using the same
  predicate every other case trusts to come back false. Without that, "no
  credential appeared" is satisfied by a run that never had one.
- **Assert the weakest true statement, not a derived one.** `oldest_date` is a
  minimum over dates actually read, not the last row of a page.

## Where things run

- `test/` — minitest, Ruby. No network, and archive-read tests assert that no
  worker is ever started.
- `pyworker/test_*.py` — pytest. `addopts = "-q"` hides the summary line; run
  `uv run pytest <file>` without `--quiet` to see counts.
- `script/smoke` — the built CLI end to end against a stub worker, in a temp XDG
  root. `--verbose` prints each command's stdout *and stderr*; it printed only
  stdout at first, which is how a wrong string in a smoke case stayed hidden.
- `rake mutants` — breaks each guard in `script/mutants.json` on purpose and
  requires some suite to fail. Not part of `rake ci`: it runs the suite once per
  mutant and edits tracked files while it works, restoring them in an `ensure`.
  Run it after changing a guard.
- `rake gitleaks` — secret scan, also a pre-commit hook. Never bypass it.
