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
      offenders = SCRIPTS.select { |path| code(path).match?(INTERACTION) }

      assert_empty offenders.map { |path| File.basename(path) },
                   "a recon script acts. Observation does not require it, and the last " \
                   "script that acted did so on a DOM nobody had observed."
    end

    def test_every_recon_navigation_goes_through_the_gate
      offenders = SCRIPTS.select { |path| code(path).match?(UNGATED_GOTO) }

      assert_empty offenders.map { |path| File.basename(path) },
                   "page.goto skips safety.assert_navigable; session.goto does not"
    end

    def test_the_deleted_driver_has_not_come_back_unannounced
      refute_path_exists File.expand_path("../../scripts/drive-cart-mutation.py", __dir__),
                         "reintroduce this with the observed DOM and the state machine, " \
                         "not by restoring the file"
    end

    private

    # Comments and docstrings discuss these verbs at length -- this file's own
    # subject is a script that clicked. Matching prose would make the alarm
    # unusable, and an alarm nobody can leave armed is not installed.
    def code(path)
      File.read(path).lines.reject { |line| line.strip.start_with?("#") }.join
    end
  end
end
