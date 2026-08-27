# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # A recon script observes. It does not act.
  #
  # This exists because of scripts/drive-cart-mutation.py, which was written,
  # reviewed, found unsafe, gated shut, and then deleted -- because the gate was
  # a single boolean and its own comment claimed it was "not openable by editing
  # this line alone", which was false. run() checked that one constant and
  # nothing else. Five of its six stated preconditions were prose. A shut door
  # whose key is one source edit is a TODO wearing a lock.
  #
  # Deleting it removed the bypass. This test is what stops it, or something
  # like it, from arriving again without the conversation. The acting code comes
  # back only alongside the observed DOM and the state machine described in
  # DESIGN.md, and when it does, this test is the thing that has to be argued
  # with first.
  class ReconToolsTest < TestCase
    SCRIPTS = Dir[File.expand_path("../../scripts/*.py", __dir__)]

    # Clicking is acting. There is no observational reason for it.
    CLICK = /\.click\s*\(/

    # Navigation is allowed, but only through the gated entry point, which runs
    # safety.assert_navigable. `page.goto` skips it.
    UNGATED_GOTO = /(?<!session)\.goto\s*\(/

    # Samples the patterns are checked against, because an alarm nobody has
    # heard is an alarm nobody has installed. Weakening either pattern has to
    # break a test, and with no violation in the tree there is nothing for it
    # to break -- so the violation is supplied here, synthetically.
    #
    # Synthetic, and deliberately so: a canary taken from real recon output
    # would only prove what that output happened to contain.
    ACTS = "control.click()"
    GATED = "session.goto(url, wait_until=\"domcontentloaded\")"
    UNGATED = "page.goto(url)"

    def test_the_click_alarm_can_sound
      assert_match CLICK, ACTS
    end

    def test_the_click_alarm_does_not_sound_on_navigation
      refute_match CLICK, GATED
    end

    def test_the_navigation_alarm_tells_the_two_apart
      assert_match UNGATED_GOTO, UNGATED
      refute_match UNGATED_GOTO, GATED
    end

    def test_there_are_recon_scripts_to_check
      # A pattern that matches nothing passes forever. See docs/TESTING.md.
      assert_operator SCRIPTS.length, :>=, 3, "the script list stopped finding anything"
    end

    def test_no_recon_script_clicks
      offenders = SCRIPTS.select { |path| code(path).match?(CLICK) }

      assert_empty offenders.map { |path| File.basename(path) },
                   "a recon script acts. Observation does not require a click, and the " \
                   "last script that clicked did so on a DOM nobody had observed."
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
