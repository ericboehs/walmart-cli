# Testing this repository

`rake` runs everything: rubocop, ruff, minitest, pytest, and `script/smoke`.

The layout of the suite is not arbitrary. It is the residue of nine defects
found in two days, seven of which were sitting inside checks that were passing
at the time. This file records what those had in common, because the mistakes
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

The last one shipped, in two commands, on every JSON invocation.

None of these is an attention failure. Assertions get written from inside the
code, and from there the return value of a print, the exit status of a process,
and the difference between two error codes are all invisible. They are the first
things the user meets.

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
  root. `--verbose` prints each command's output.
- `rake gitleaks` — secret scan, also a pre-commit hook. Never bypass it.
