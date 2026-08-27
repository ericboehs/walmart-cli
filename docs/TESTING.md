# Testing this repository

`rake` runs everything: rubocop, ruff, minitest, pytest, and `script/smoke`.

The layout of the suite is not arbitrary. It is the residue of 71 recorded defects
found in two days, over half of which were sitting inside checks that were
passing at the time — including one found while writing the fix for another, in this
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
| a reader that promised never to raise | it rescued one error class, and the layers under it raised `ArgumentError`, `TypeError` and `Errno::*` on sick input — so the crash landed in `damaged_ids`, which `order sync` calls *before* opening the worker, and a damaged archive crashed the repair path built for it |
| an unreadable record | `order list` printed the row like any other while `order show` refused it and `order search` omitted it — three commands disagreeing, none explaining, and the JSON said `healthy: false` without saying what was unhealthy |
| identifier-shaped keys kept | a first name passes any "looks like a field" test, so the canaries using an email and a UUID proved nothing about the claim |
| a red commit that landed | the gates ran, a test failed, the commit was chained after them and the non-zero exit scrolled past; the repository had no hooks at all |
| a bare `input()` in the capture script | Playwright's sync API only dispatches handlers while this thread is inside it, so the wait was a stopped recorder; `scripts/recon.py` had a `pump` for exactly this, with a test explaining it, and the newer script blocked anyway |
| `"body_shape": "null"` | written for a response whose body had raised on read, which is indistinguishable from a server that answered null |
| a note taken at the wrong moment | the run said "recorded 0 mutating requests and 0 responses" in a file holding one of each |
| a verdict with three outcomes | one request, one 200 and an unread body returned "got what it came for" |
| a hand-written list of narrative files | six names, and the retracted claim was in the seventh -- `pyworker/browser.py`, the code that justifies how we observe |
| a test that spun instead of failing | it waited for a pump that a mutant had removed, so the harness waited too, for eighty-seven minutes |
| a mutation harness with no timeout | a hang was not one of its outcomes, and cancelling is the one path that leaves the tree mutated |
| a test pinned to one spelling of the evidence | it read `entry["body"]` from live captures and stopped checking anything the day the format dropped bodies |
| a count kept in my head | every commit message from `e7ca045` on reported a mutant total arrived at by addition, never by counting; the drift reached fifteen, and the defect tally was thirteen high |
| the unknown named wrong | the rewritten docstring still called the mutation unobserved when the request had been seen four times and the response is what is missing |
| a parity test that read one file | it passed while the capture script's own --help repeated the whole retracted diagnosis, and the confidence it produced covered everything |
| "both were true when written" | the comment in the parity test granted an inference the standing of an observation, inside the test written to stop exactly that |
| a document repeating a retracted diagnosis | CONTRACTS.md still said the mutation runs off the main thread and that no mutation code exists, months after evidence and a commit disproved each |
| a flag that no longer did anything | `--scrub-literal` survived the removal of the last text path and would have accepted a value it could not act on |
| a bound measured in the wrong place | the cap stopped the recursion but the root walk still visited every entry to count them; the test counted recursive calls, so it passed |
| breadth uncapped while claiming otherwise | the comment said depth, breadth and length were bounded; only depth and length were |
| a shape that kept every key | "carries the contract and none of the contents" was false: a map keyed by email or cart id puts the secret in the key position, where nothing was looking |
| a list described by element zero | the line we care about may not be first, and an errors array is heterogeneous by nature |
| a prompt that promised to abort | a forbidden operation is counted and the run continues; telling someone the tool will stop them is worse than telling them nothing |
| three tests passing for the wrong reason | the order-flow, allowlist and same-host response tests all used an uncleared request, so the correlation check refused them before the guard under test was reached; three mutants survived and named it |
| a guard tested against an imagined caller | the store check was tested with a bare id string while Ruby sends the whole store object; both suites passed and every correctly configured production request would have been refused |
| one gate for two unknowns | per-line delivery was "documented as unproven", so capturing a response would have silently authorised a second, unrelated missing contract |
| the phase flag set too early | `sent = True` preceded the send's own safety checks, so a refusal that sent nothing would have been reported as possibly-sent |
| a prompt for the impossible | the CLI asked permission, then refused — and printed the refusal twice, because two whole messages were glued together |
| a name accepted as an id | `cart add milk` reached the worker; item ids are digits and nothing checked |
| a success we invented | `acknowledgement` accepted any non-empty `data`, so `{"data":{"anything":false}}` was a success — fail-open, for a shape never observed |
| the error text side door | GraphQL errors were passed through verbatim into the result, and a server echoing its input would have carried the cart id past every canary |
| guards for a store nobody pinned | the mutation ignored `pinned_store` and the cart's own `store_id`/`intent`, so it would change a pickup cart or another store's cart |
| the first duplicate wins | `find_line` returned the first match, quietly making "exact variant" mean "first line with this id" |
| `bool("false")` | remove was read with `bool()`, so the string `"false"` deleted a line; and `cart.add` with `remove:true` removed one |
| a maybe that was a no | failures before the request was sent were reported as unverified, sending someone to check a cart that provably never changed |
| tests under `private` | twenty new tests were inserted after a `private` keyword; minitest collected none of them and the suite count did not move enough to notice |
| invented key names | the mutation module read `cart["items"]` and `line["first_party"]`, neither of which the cart parser produces — written from memory instead of from the parser's output |
| a grep that matched its own excuse | a test asserting no absolute-quantity flag existed grepped the source for "absolute" and matched the comment explaining why absolutes are forbidden |
| the guard and the record disagreed | the capture validated the operation with one function and labelled the evidence with another, so every request in the first capture was recorded as its 64-character persisted hash |
| the canary was the leak | the commit that scrubbed a live identifier copied that same identifier into a tracked regression test, so the fix published what it removed |
| absence read as agreement | the capture rejected bodies that disagreed with the URL and accepted bodies that named nothing, letting unclassified payloads into the evidence file |
| a guard off the path | `is_forbidden_operation("CreateContract")` returned true while `assert_navigable` let the same URL through — the check existed and nothing consulted it |
| a new guard, old lessons | the operation guard matched raw and case-sensitively, so `%72eserveSlot` and `/GraphQL/` both walked past disciplines the URL guard had learned years earlier |
| a malformed `sync` value | an Integer crashed `synced?`, an Array crashed the field lookup, a String rendered `stopped early ()` — and an Array *also* read as never-synced, turning corruption into absence |
| `cutoff` with no window | settled by reason alone, so a globally incomplete archive exited 0 and every later read stayed silent |
| a finished walk | `complete` from the worker means "no next cursor", so a bounded `--year` that reached the end reported the archive as all of history while it had deliberately skipped every newer order — and printed nothing, because only a cutoff counted as scoped |
| `--year` | help, design and the class comment all said "one calendar year"; the request carried only a lower bound, so in 2026 `--year 2024` archived 2024, 2025 and 2026 |
| the promise `--full` broke | protected records rode in `known_ids`, which `--full` empties and the worker ignores under full — so the pre-flight printed "these will be left untouched" and `--full` then fetched every one of them |
| `--full` on a newer record | the reader correctly refused a format it did not understand, and the writer then treated "unreadable" as "repairable" and overwrote it |
| "known" order ids | meant "the index mentions it", not "the archive can return it", so an entry outliving its file told the worker to skip that order on every sync — and the unchanged-shortcut then discarded the replacement, making the damage permanent |
| the cutoff | it stopped the walk on the *first* row older than the window, dropping every later row on that page — while `_oldest_date`, in the same file, refuses to trust row order for exactly that reason |
| `cutoff` as a verdict | `order sync --year` exited 0 in silence, then `order list` called the same run "stopped early" and advised re-running it, which reaches the identical cutoff every time |
| the response observer | `on_response` subscribed on the *context*, so it heard every tab in the browser; the tests passed because in a launched browser there is only one tab, and the second tab only exists in the mode we added last |
| the worker's CDP endpoint | loopback was enforced in Ruby only, and the worker executes what it is handed; the protocol is directly invocable, so the check sat outside the boundary it was protecting |
| the archive's pagination cursor | a stop note persisted the cursor "so the next run can pick up where it left off"; nothing ever read it, `order sync` restarts at page one, and the test named "sync state survives a restart" asserted only that the field round-tripped |
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

### Two numbers, counted rather than remembered

71 defects in the table below. 164 mutants in `script/mutants.json`.

Both are written as numerals and both are checked by
`test/walmart/documentation_parity_test.rb`, because both were wrong. Every
commit message from `e7ca045` onward reported a mutant count arrived at by
adding new entries to the previously reported figure, and the drift reached
fifteen. The defect tally was maintained the same way and was thirteen high.
Nobody was lying and nobody checked; the number was a running total in someone's
head, restated with the authority of a measurement.

A number in prose that describes something countable in the repository should
be counted by a test.

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
you, which is exactly why so many of these were sitting in checks that passed. Two
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

It rewrites tracked files in place and restores them, so it refuses to start if
a file in the plan has uncommitted changes, and says on stderr that the tree is
dirty while it runs. Both are for the bystander as much as for the author: a
second person running `git status` mid-run sees a modified source file with no
way to tell a mutation from someone's edit. That happened here, on
`lib/walmart/secrets.rb`, and the reviewer did the right thing — refused to
inspect or reset a file they had not modified, and stopped to ask.

## Independent review is part of the method

The implementation author and the reviewer should not do the same job twice.
The author knows the intended contract and can inspect the places a guard must
reach. The reviewer should distrust the summary, run the public command, read
stdout, stderr, and status, and hand the check a different failure. Neither view
substitutes for the other: the author can find a serializer bypass inside a
class the reviewer cannot safely inspect; the reviewer can find a correct guard
wired to the wrong stream, process, tab, or exit path.

This is adversarial only in the useful sense. Reproduce claims instead of
accepting them, and report the boundary of what was not independently checked.
A passing suite produced by the person who wrote both the behaviour and its
fixture is evidence, not independent evidence. Review becomes stronger when a
second person can run the same command or mutant and read the verdict directly.

New operating modes especially need that split. CDP attachment preserved the
own-tab invariant for navigation and violated it for response observation; the
same author wrote both the mode and its passing ownership tests. The defect was
visible only after a reviewer re-asked the old invariant across every operation,
not just the operation named in the test.

## A test that prevents, not one that verifies

Most checks here catch a defect after it exists. A few fire *before* the mistake
is committed, and they are all the same shape: a completeness test that compares
one artifact against a list maintained somewhere else.

The help-table test named four commands missing from the table while the table
was still being written. Adding `browser start` tripped three at once, unasked —
help entries, exit-code coverage, and flag units — none of which I had thought
about while writing the command. Editing a validation line broke the pattern of
the mutant guarding it, and the mutation-plan audit said so rather than letting
the mutant quietly stop applying.

That is the return on this shape, and it is why the count of them matters more
than the count of any other kind of test here.

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
- **A predicate is a claim about a fact, and the fact can move.** `archived?`
  asked the index; every caller meant "can we produce this order". Those agreed
  until a file went missing, and then two of them were wrong in the same
  direction: skip the fetch, and discard the fix. When one predicate serves
  several questions, ask what each caller would do if it were wrong.
- **"Never raises" is a claim about every input, so enumerate them.** The
  promise was written next to a rescue of one error class. The way to keep a
  promise like that honest is a table of sick inputs, not a sentence — ours has
  ten rows and every one of them escaped the first version.
- **A new guard inherits none of the old guard's scars.** The URL guard decodes,
  bounds the decoding, parses paths, and matches case-insensitively — every one
  of those a defect once. The operation guard written beside it did none of
  them, and failed in the same four ways within a week. When you add a check
  next to an existing one, read what the old one survived.
- **A regression test for "this value must not persist" is the last place that
  value may appear.** Both halves of the fix passed their own review — the
  capture no longer held the id, the scrubber demonstrably worked — and the
  identifier was published anyway, as the canary. Canaries are synthetic.
- **A lesson that lives in one file is a lesson about that file.** `recon.py`
  had the pump, the comment and the test; the capture script was written later
  and blocked anyway. Fixing a bug does not immunise the next thing you write.
- **A test must fail, not hang.** Anything that waits for a condition needs a
  deadline, or the mutant that breaks the condition breaks the harness instead
  of being caught by it.
- **A hang is not an answer.** Any runner that waits on a child needs a
  timeout, and hanging has to be reported as its own outcome.
- **Derive the list, do not remember it.** A hand-written set of files to check
  is a set of files someone will forget to extend.
- **A discipline nobody enforces is a habit, and a habit does not fail
  loudly.** The gates were being run by hand before every commit, which worked
  until the once it did not. `rake hooks` installs it, and it runs the CI set
  -- rubocop, ruff, both suites, smoke. Not gitleaks, and not mutants, which
  edit files and take minutes; a green commit means the CI set passed and not
  that every gate did.
- **Name the unknown precisely.** A docstring calling the mutation unobserved
  was disproved by its own opening line, which said the request is known;
  the response is the missing half. Being vague about which half is missing is
  how a gate gets argued away.
- **Parity with one file is not parity.** Enumerate every file that can state
  the claim, executables included -- a docstring is `--help`, which is the most
  read prose in a CLI.
- **A retracted claim may be quoted only by a sentence that says it is false.**
  That is the difference between a history and a restatement, and it is
  checkable.
- **An inference that was later disproved was never "true when written".**
  Calling it that grants it evidence it never had, and turns a reasoning
  failure into a maintenance one.
- **A retraction has to reach the document, not just the commit message.** The
  correction lived in a commit and a lesson; the contract went on stating the
  disproven claim as present fact, next to a section contradicting it.
- **Give the rotting claims a parity test.** Where a document asserts something
  about code -- that a file does not exist, that a field is not written -- the
  assertion can be checked. Four of them now are.
- **Mutate the document, not the test that reads it.** A mutant that turns an
  assertion into a tautology proves nothing, because nothing tests a test.
- **Keep only what the run came for.** A capture aimed at one unknown should
  not re-collect the known thing beside it; the second copy is all cost.
- **A test of a bound must measure the thing being bounded.** Counting
  recursive calls proved nothing about a loop that never recursed. Count what
  was consumed.
- **Give a test an input that exceeds the limit.** The all-known case had fewer
  keys than the budget, so the assertion held whether or not anything was
  capped; shrink the budget until the input genuinely overruns it.
- **A pattern cannot separate a field name from data shaped like one.** Only a
  list of names already seen can. The gate is an allowlist; unknown keys become
  a count, and the count is what asks for a second look.
- **Keys are values.** Anything copied verbatim out of an unseen document has
  to earn it -- schema-shaped and short -- and the rest collapse into a marker
  that counts them without quoting them.
- **An equivalent mutant is a report of dead code.** When removing a branch
  changes no behaviour and breaks no test, delete the branch; do not write a
  test to defend it.
- **Never provoke a failure to learn its shape.** A rejection that arrives on
  its own is evidence; one that had to be caused is a request we had no reason
  to make. Adversarial cases are derived from the predicate instead.
- **A guard must be the only thing that can stop the request it is asked
  about.** If an earlier check refuses first, the test proves the earlier
  check. Clear the way to the guard deliberately.
- **An alarm that cannot sound is not installed.** Where a denylist sits behind
  an allowlist it can never be the thing that refuses, so its only observable
  effect is the count -- assert the count.
- **A flag can be toggled, so make toggling insufficient.** Each closed gate has
  a dormant test that fails the moment the constant flips without the work it
  stands for.
- **A test may not imagine the other side of a boundary.** Where Ruby produces
  and Python consumes, the shape lives in `test/contracts/` — the producer's
  test asserts it still emits that, the consumer's test reads it. Two suites
  passing says nothing about whether they agree.
- **One gate per unknown.** Two unproven things behind one flag means evidence
  for either opens both. Each contract gets its own constant and its own
  sentence.
- **A prompt is a promise that the answer matters.** Refuse before asking, not
  after.
- **An unobserved contract is a gate, not a caveat.** Writing "the response
  shape is unobserved" in a docstring and then accepting any non-empty `data`
  is the same bug as not knowing: the honest encoding is a constant that is
  `None`, a check that fails closed, and a feature that refuses to run. The
  tests inject a hypothetical shape through a fixture whose name says it is a
  hypothesis, so nothing reads as evidence.
- **A canary only proves what it was given.** The cart-id tests used errors
  that never contained the id, so they proved happy-path values stay in. The
  realistic leak is Walmart echoing the offending variables back; the canary
  now arrives inside a server error.
- **Refuse ambiguity rather than resolving it.** Two cart lines with one item
  id is not a tie to be broken by order.
- **A declared action is a reachable action.** `cart.increment` and
  `cart.decrement` sat on the worker surface, implemented nowhere and exposed
  by no command. The protocol is invocable directly, so that is a way in
  rather than a placeholder.
- **A decision table belongs in the test as data.** Fourteen rows of
  (acknowledgement, before, after, target) caught a case reasoning had missed:
  a refusal with no usable pre-read was being reported as a clean failure, when
  "unmoved" cannot be established without a before. Reading the policy in one
  block is what made the hole visible.
- **Assert the property beside the table.** The rows say what each case yields;
  a separate test says APPLIED implies both acknowledgement and a matching
  post-read. If a row is ever wrong, the property still fails.
- **Check the test ran.** Twenty tests landed under a `private` and minitest
  quietly collected none of them. Watch the count move by the number you added.
- **The guard and the record must answer from the same code.** Two functions
  computed the operation: the validated one gated the request, the naive one
  wrote the document. The safety decision was correct and the evidence
  describing it was wrong — the more dangerous way round, because nobody
  rereads a document they believe was validated. I had read that capture and
  reported the operation correctly, from the URL by eye, never noticing the
  field beside it disagreed.
- **Rejecting disagreement is not requiring agreement.** A body that names
  nothing disagrees with nothing, so a mismatch check admits everything
  unclassified. Discriminate positively, on the shape actually observed.
- **A denylist cannot be the guarantee, only the alarm.** No list of forbidden
  operation names can enumerate what Walmart has not shipped yet. The capture
  and the mutation path are allowlists — they keep and send only what they came
  for — and the denylist sits behind them to make noise.
- **Fixing the status is not fixing the story.** The exit code learned to read
  the whole note; the sentence next to it still matched on the reason alone, so
  the states the new object existed to catch exited 3 in silence. When a
  predicate gets smarter, find every consumer of the old one.
- **A value object is only as total as its worst input.** `SyncNote.from` was
  written for a Hash because that is what the writer writes. The file is on
  disk.
- **A word can be a success or a failure depending on state it does not
  carry.** `cutoff` means "we reached the bottom of the window we were given",
  which is only a success if there was a window. Membership in a list of
  reasons cannot express that, so the predicate had to become a property of the
  whole note.
- **Saying something is not saying the right thing.** The scoped-window sentence
  and the unreadable-record warning shared a branch, so printing either
  satisfied the check that anything was printed at all.
- **A name inherited from a lower layer carries that layer's meaning, not
  yours.** `complete` meant "no next cursor" in the worker and "the archive has
  everything" in the CLI, and the word travelled between them unchanged. The
  worker was never wrong; the reader was, and the shared spelling is what hid
  it.
- **Three places agreeing with each other is not evidence.** The help, the
  design and the class comment all said "one calendar year", which made the
  claim feel checked. None of them was the code. Agreement among descriptions
  measures how consistently something was described, not whether it is true.
- **When two rules disagree, the one that admits ignorance wins.** A file with
  a newer format *and* a mismatched id was judged by the id first, called
  repairable, and destroyed. Order your checks so the one saying "I do not
  understand this" runs before the ones that presume they do.
- **A list that means two things will be right for one of them.** `known_ids`
  carried "we already have it" and "we must not overwrite it"; `--full` empties
  the first meaning and silently took the second with it.
- **Refusing to read something and then writing over it is the worst pairing
  available.** Two correct-looking decisions: the reader would not guess at a
  format it did not understand, and the writer treated anything unreadable as
  repairable. Between them they destroyed the only copy. When one component
  says "I cannot understand this", ask what the others do with that answer.
- **A guard written for the healthy case can break the sick one.** The new
  containment check canonicalised the orders directory before deciding anything,
  and an archive can hold an index with no orders directory at all — the exact
  state the check existed to diagnose. It raised a raw ENOENT instead. Resolve
  what you know exists, not what you hope does.
- **Hardening one consumer of an assumption does not harden the others.**
  `_oldest_date` carries a docstring explaining that Walmart pages can be out of
  order, and that deriving a claim from row order makes one bad page into a
  confident lie. Twenty lines away the cutoff bet completeness on row order.
  The author saw the risk, fixed the consumer in front of them, and did not go
  looking for the second one. When you write "we cannot assume X", grep for X.
- **A safety property proved in one mode is not proved in the other.** "We touch
  only our own tab" was true of navigation and false of observation, and every
  test passed, because a launched browser has one tab and the second tab is only
  possible in attached mode. When a new mode arrives, the old invariants need
  re-asking in it, not inheriting.
- **Validation belongs at the boundary that executes, not the one that asks.**
  `cdp_url` was checked in Ruby and used in Python. The worker is a separate
  process precisely so it need not trust its caller, and its protocol is
  directly invocable. Both copies are written independently, from the spec
  rather than from each other, so a blind spot in one is unlikely to be a blind
  spot in both.
- **Proving the writer wrote is not proving a consumer exists.** A cursor was
  written to disk, described in a comment as enabling resume, and read by
  nothing. The test round-tripped it through the writer and the reader and
  passed forever. The regression that would have caught it asserts the
  *opposite* direction: hand the next run a cursor and watch the request omit
  it. When a field exists for a downstream purpose, test the downstream.
- **A capability in a name is a claim.** `resumable?` meant "reason is not
  complete". It was read, by us, as "the next run can continue from here", and
  the user-facing hint said so. Renamed to `incomplete?`, which is what it
  knows.
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
  lowered stops the walk early with an incomplete archive; raised, it is an
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
  label kept verbatim, a walk that stops with a reason and an incomplete archive.
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
