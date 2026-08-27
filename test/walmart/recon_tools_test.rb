# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # A static alarm over the interaction methods a recon script must not call.
  #
  # Read that sentence rather than a larger one. This is not proof that a recon
  # script does not act, and an earlier version of this file claimed it was --
  # the class comment said "A recon script observes. It does not act." while the
  # code checked for two literal method names. `press`, `fill`, `dispatch_event`
  # and a side effect inside `evaluate` were all outside the net the prose was
  # standing on. A denylist cannot be the guarantee, only the alarm, and the
  # comment above a denylist is the most likely place for it to be mistaken for
  # one.
  #
  # What it does check: every file in scripts/, whatever its extension, for the
  # named interaction methods below, and for navigation that skips the gated
  # entry point.
  #
  # Why it exists: scripts/drive-cart-mutation.py was written, reviewed, found
  # unsafe, gated shut behind a single boolean whose comment claimed to be more
  # than a single boolean, and finally deleted. See "Driving a cart mutation:
  # what must exist first" in DESIGN.md. Acting code returns with the observed
  # DOM and the state machine described there, and this alarm is the thing that
  # has to be argued with on the way.
  class ReconToolsTest < TestCase
    # Every file, not every .py file: a script that acts is no less a script
    # for being written in something else.
    SCRIPTS = Dir[File.expand_path("../../scripts/*", __dir__)].select { |path| File.file?(path) }

    # The observer is not in scripts/ -- it has no entrypoint and cannot be run
    # -- but it is the code that will one day be pointed at a real cart page,
    # so it is held to the same alarm. A file escapes the interaction check by
    # living somewhere the check does not look, and that is not a property
    # anyone chose.
    OBSERVERS = Dir[File.expand_path("../../pyworker/observ*.py", __dir__)]
    WATCHED = SCRIPTS + OBSERVERS

    # Playwright's interaction surface, as far as we use or might use it.
    # Anything that moves a pointer, moves focus, changes a value, or hands
    # arbitrary code to the page.
    #
    # `evaluate` is here because a read can be spelled as a write: the return
    # value may be innocent while the expression is not.
    INTERACTIONS = %w[
      click dblclick press press_sequentially fill focus hover tap
      check uncheck set_checked select_option select_text type
      drag_to set_input_files evaluate evaluate_handle dispatch_event
    ].freeze

    INTERACTION = /\.(?:#{INTERACTIONS.join("|")})\s*\(/

    # Navigation is allowed, but only through the gated entry point, which runs
    # safety.assert_navigable. `page.goto` skips it.
    UNGATED_GOTO = /(?<!session)\.goto\s*\(/

    # Samples the patterns are checked against, because an alarm nobody has
    # heard is an alarm nobody has installed. Weakening a pattern has to break
    # a test, and with no violation in the tree there is nothing for it to
    # break -- so the violations are supplied here, synthetically.
    #
    # Synthetic, and deliberately so: a canary taken from real recon output
    # would only prove what that output happened to contain.
    GATED = 'session.goto(url, wait_until="domcontentloaded")'
    UNGATED = "page.goto(url)"

    def test_every_named_interaction_is_caught
      INTERACTIONS.each do |method|
        assert_match INTERACTION, "control.#{method}()",
                     "#{method} is listed as forbidden and the pattern does not catch it"
      end
    end

    def test_the_interaction_alarm_does_not_sound_on_reading
      ["session.goto(url)", "locator.count()", "response.json()", "page.url"].each do |sample|
        refute_match INTERACTION, sample
      end
    end

    def test_the_navigation_alarm_tells_the_two_apart
      assert_match UNGATED_GOTO, UNGATED
      refute_match UNGATED_GOTO, GATED
    end

    def test_there_are_recon_scripts_to_check
      # A pattern that matches nothing passes forever. See docs/TESTING.md.
      assert_operator SCRIPTS.length, :>=, 3, "the script list stopped finding anything"
    end

    def test_no_recon_script_calls_an_interaction_method
      offenders = WATCHED.select { |path| code(path).match?(INTERACTION) }

      assert_empty offenders.map { |path| File.basename(path) },
                   "a recon script acts. Observation does not require it, and the last " \
                   "script that acted did so on a DOM nobody had observed."
    end

    def test_the_observer_is_watched_by_the_same_alarm_as_the_scripts
      # Named, because a derived list that quietly became empty would make the
      # test above pass by having nothing to check.
      assert_includes WATCHED, File.expand_path("../../pyworker/observe_walk.py", __dir__)
      assert_includes WATCHED, File.expand_path("../../pyworker/observation.py", __dir__)
    end

    PAGE_DUMP = /\.content\s*\(|screenshot|accessibility|inner_html|\.snapshot\s*\(/

    # Synthetic, because an alarm with no sample is one nobody has heard. Each
    # line is a way somebody could reasonably write the worst artefact this
    # project could produce.
    DUMPS = [
      "    html = page.content()",
      "    page.screenshot(path=out)",
      "    tree = page.accessibility.snapshot()",
      "    markup = node.inner_html()",
      "    shot = locator.snapshot ()"
    ].freeze

    def test_the_page_dump_alarm_can_actually_sound
      DUMPS.each { |line| assert_match PAGE_DUMP, line }
      refute_match PAGE_DUMP, "    value = reader.read(node.get_attribute, name)"
      refute_match PAGE_DUMP, "    text = reader.read(nodes.nth(index).text_content)"
    end

    def test_the_observer_never_dumps_a_page
      offenders = OBSERVERS.select { |path| code(path).match?(PAGE_DUMP) }

      assert_empty offenders.map { |path| File.basename(path) },
                   "a page dump is the one artefact this project must never produce"
    end

    def test_the_observer_probes_only_the_attribute_names_it_declared
      body = code(File.expand_path("../../pyworker/observe_walk.py", __dir__))
      # Every line mentioning the probe, whatever shape the call takes. The
      # first version of this looked for `get_attribute(...)` and found
      # nothing, because the real call hands the method to the reader and
      # passes the name alongside it. An alarm that assumes a call shape is an
      # alarm watching a spelling.
      probes = body.lines.grep(/get_attribute/)

      refute_empty probes, "if nothing probes attributes this alarm is watching nothing"
      probes.each do |probe|
        assert_match(/attribute\.value/, probe,
                     "an attribute probe named something other than the declared list")
      end
    end

    def test_every_recon_navigation_goes_through_the_gate
      offenders = WATCHED.select { |path| code(path).match?(UNGATED_GOTO) }

      assert_empty offenders.map { |path| File.basename(path) },
                   "page.goto skips safety.assert_navigable; session.goto does not"
    end

    def test_the_deleted_driver_has_not_come_back_unannounced
      refute_path_exists File.expand_path("../../scripts/drive-cart-mutation.py", __dir__),
                         "reintroduce this with the observed DOM and the state machine, " \
                         "not by restoring the file"
    end

    # docs/DOM-OBSERVATION.md opens by saying there is no page-reading observer
    # and nothing has been observed. That is true today and will stop being
    # true, and a status line nobody checks is the exact failure this
    # repository keeps recording: a promise outliving its mechanism. So the
    # mechanism is here.
    #
    # The claim is deliberately narrower than it was. The vocabulary the
    # observer will use is implemented, in pyworker/observation.py, and a
    # status line saying "no tool exists" while a module of that tool exists
    # is the kind of nearly-true sentence that stops being read. What must
    # stay absent is anything that can read a page or be run.
    OBSERVATION_DESIGN = File.expand_path("../../docs/DOM-OBSERVATION.md", __dir__)
    NO_OBSERVER = "There is no runnable command, nothing constructs a browser, " \
                  "and nothing has been observed."

    def test_the_observation_design_still_says_nothing_runnable_exists
      assert status_claims_no_observer?(File.read(OBSERVATION_DESIGN)),
             "the design's status line has stopped saying the observer cannot be run; " \
             "either that is false, or this test is the thing that is out of date"
    end

    # The observer is code now, so "does it exist" stopped being the question.
    # What must stay true is that it cannot be run and cannot open a browser by
    # itself -- a module somebody has to wire up deliberately, reviewed before
    # it ever sees a page.
    def test_the_observer_has_no_way_to_be_run
      observer_paths(File.expand_path("../..", __dir__)).each do |path|
        body = code(path)

        refute_match(/if __name__/, body, "#{File.basename(path)} has an entrypoint")
        refute_match(/argparse|sys\.argv/, body, "#{File.basename(path)} parses arguments")
        refute_match(/sync_playwright|connect_over_cdp|new_context|launch\(/, body,
                     "#{File.basename(path)} builds a browser")
        refute_match(/^\s*import\s+browser|from\s+browser\s+import/, body,
                     "#{File.basename(path)} reaches for a session")
      end
    end

    # The live-run checklist. Three kinds of sentence in it are load-bearing,
    # and each is the kind that gets softened by someone in a hurry rather than
    # deleted outright -- so each is pinned here.
    #
    # This checks only what the document must never stop saying. It is not a
    # parity test for the gates themselves: those are judgements a reviewer
    # applies, and a test asserting that a paragraph exists would measure its
    # presence while saying nothing about whether anyone applied it.
    LIVE_REVIEW = File.expand_path("../../docs/DOM-LIVE-REVIEW.md", __dir__)
    NOT_A_RUNBOOK = "this is a review checklist, not a runbook. No runnable observer exists."
    FRESH_AUTHORISATION = [
      /fresh, explicit authorisation/i,
      /naming \*\*the exact commit\*\*/i,
      /naming \*\*one\*\* observation attempt/i
    ].freeze
    NO_RETRY = /no automatic retry of anything, for any reason/i
    NO_MUTATION = /any mutation of the cart, any quantity change/i
    # An enforceable boundary rather than an absolute nobody can keep: loading
    # a cart page makes Walmart's own scripts issue background requests. The
    # line that can be held is between traffic this tool authors and traffic
    # the page generates by being open.
    NO_TOOL_TRAFFIC = /no direct POST, no request API, no\s+GraphQL operation/i
    # Gate 10, pinned on both sides. The flat phrasing is caught by the
    # retraction net in documentation_parity_test; these pin what replaced it,
    # because a claim can also be lost by deletion.
    OBSERVATION_LICENSES = [
      /does not by itself authorize or activate acting\s+code/i,
      %r{may\s+establish part of the observed DOM/control contract}i
    ].freeze

    def test_the_live_checklist_says_it_is_not_a_runbook
      assert_includes squish(File.read(LIVE_REVIEW)), NOT_A_RUNBOOK,
                      "the checklist has stopped disclaiming itself; a document that reads " \
                      "like instructions is one someone will follow"
    end

    def test_the_live_checklist_keeps_its_three_mandatory_clauses
      body = File.read(LIVE_REVIEW)

      FRESH_AUTHORISATION.each do |clause|
        assert_match clause, body, "the authorisation gate lost a required clause"
      end
      assert_match NO_RETRY, body, "the no-retry clause is gone"
      assert_match NO_MUTATION, body, "the exclusion of cart mutation is gone"
      assert_match NO_TOOL_TRAFFIC, body, "the exclusion of tool-authored requests is gone"
      OBSERVATION_LICENSES.each do |clause|
        assert_match clause, body,
                     "Gate 10 no longer says what an observation does and does not license"
      end
    end

    def test_the_live_checklist_does_not_carry_commands_to_paste
      body = File.read(LIVE_REVIEW)
      offenders = body.lines.grep(%r{^\s*(?:\$|>|bin/walmart |uv run |cd pyworker)})

      assert_empty offenders,
                   "the checklist has grown runnable commands, which is what a runbook is:\n" \
                   "#{offenders.join}"
    end

    def test_no_observer_lives_in_scripts_where_the_runnable_things_are
      assert_empty Dir[File.expand_path("../../scripts/observe*", __dir__)],
                   "scripts/ is where a thing gets run from; the observer is not ready for that"
    end

    # Both sides of that assertion are true today, so it would pass with either
    # side broken. An alarm with no sample is one nobody has heard, and two
    # mutants survived here before these canaries existed.
    def test_the_status_check_would_notice_an_observer_appearing
      Dir.mktmpdir do |root|
        FileUtils.mkdir_p(File.join(root, "scripts"))
        FileUtils.mkdir_p(File.join(root, "pyworker"))

        assert_empty observer_paths(root)

        File.write(File.join(root, "scripts", "observe-cart-dom.py"), "")

        assert_equal 1, observer_paths(root).length

        File.write(File.join(root, "pyworker", "observe_cart.py"), "")

        assert_equal 2, observer_paths(root).length,
                     "an observer living in pyworker/ is still an observer"
      end
    end

    def test_the_status_check_would_notice_the_sentence_going_missing
      refute status_claims_no_observer?("Status: the observer is implemented and runnable.")
      assert status_claims_no_observer?(
        "Status: the walker is implemented. There is no runnable command,\nnothing " \
        "constructs a browser, and nothing has been observed."
      )
    end

    def test_the_vocabulary_module_is_not_mistaken_for_the_observer
      # It is allowed to exist while the status line says no observer does,
      # because it cannot read anything. If that ever changes the interaction
      # and navigation alarms above will not see it -- they scan scripts/ --
      # so it is named here.
      vocabulary = File.expand_path("../../pyworker/observation.py", __dir__)

      assert_path_exists vocabulary
      body = code(vocabulary)

      # Code shapes, not the word. The module's own prose says "page" often,
      # because describing what it refuses to read is most of its subject.
      refute_match(/^\s*(from|import)\s+playwright/, body)
      refute_match(/\bpage\.\w/, body)
      refute_match(/\.locator\(|get_by_role\(|query_selector/, body)
      refute_match(/async def|await /, body)
    end

    private

    def observer_paths(root)
      Dir[File.join(root, "scripts", "observe*")] + Dir[File.join(root, "pyworker", "observe*.py")]
    end

    # Normalised, because the sentence is wrapped in the document and a status
    # check defeated by a line break is a status check nobody has.
    def status_claims_no_observer?(text)
      squish(text).include?(NO_OBSERVER)
    end

    def squish(text)
      text.gsub(/\s+/, " ")
    end

    # Comments and docstrings discuss these verbs at length -- this file's own
    # subject is a script that clicked. Matching prose would make the alarm
    # unusable, and an alarm nobody can leave armed is not installed.
    def code(path)
      File.read(path).lines.reject { |line| line.strip.start_with?("#") }.join
    end
  end
end
