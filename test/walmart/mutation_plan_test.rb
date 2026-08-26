# frozen_string_literal: true

require "test_helper"
require "json"

module Walmart
  # script/mutants.json is the inventory of guards whose failure is silent, which
  # makes it the easiest file in the repo to let rot: rename a constant, and the
  # mutant that proved the guard works stops applying. `rake mutants` would say
  # so, but only if someone runs it, and the whole reason the plan exists is that
  # these guards have no natural caller to notice them.
  #
  # So the plan is checked the way the guards are: statically, in the suite that
  # runs on every commit. This is the cheap half. The other half -- a new guard
  # arriving with no entry -- cannot be caught mechanically, and is written down
  # as a habit in docs/TESTING.md instead of pretended about here.
  class MutationPlanTest < Minitest::Test
    PLAN_PATH = File.expand_path("../../script/mutants.json", __dir__)
    PLAN = JSON.parse(File.read(PLAN_PATH)).freeze

    def test_the_plan_is_not_empty
      # Guards against the failure where every other test here passes vacuously.
      refute_empty PLAN
    end

    def test_every_mutant_names_a_file_that_still_exists
      PLAN.each do |mutant|
        path = File.expand_path("../../#{mutant["file"]}", __dir__)

        assert_path_exists path, "#{mutant["label"]}: names a file that is gone"
      end
    end

    # The rename case, and the reason this test is worth more than an existence
    # check: a guard can be alive and well while the mutant that proves it works
    # quietly stops applying to anything.
    def test_every_mutant_still_matches_exactly_one_place_in_its_file
      PLAN.each do |mutant|
        path = File.expand_path("../../#{mutant["file"]}", __dir__)
        # Checked again here so a deleted file fails with a sentence rather than
        # an ENOENT backtrace from File.read.
        assert_path_exists path, "#{mutant["label"]}: names a file that is gone"

        occurrences = File.read(path).scan(mutant["from"]).length

        assert_equal 1, occurrences,
                     "#{mutant["label"]}: its pattern matches #{occurrences} places in " \
                     "#{mutant["file"]}. A mutant that does not apply proves nothing, and " \
                     "an ambiguous one mutates a line nobody chose."
      end
    end

    def test_no_mutant_is_a_no_op
      PLAN.each do |mutant|
        refute_equal mutant["from"], mutant["to"], "#{mutant["label"]}: changes nothing"
      end
    end

    # Duplicated labels make the report unreadable in exactly the situation it
    # matters -- several survivors at once.
    def test_every_mutant_is_named_and_named_distinctly
      labels = PLAN.map { |mutant| mutant["label"] }

      assert_empty(labels.select { |label| label.to_s.strip.empty? })
      assert_equal labels.uniq, labels
    end

    # A Python guard "verified" by a Ruby suite that never imported it would be
    # the next instance of the pattern this whole file exists for.
    def test_a_python_mutant_names_a_python_suite
      PLAN.select { |mutant| mutant["file"].end_with?(".py") }.each do |mutant|
        command = Array(mutant["command"]).join(" ")

        assert_includes command, "pytest",
                        "#{mutant["label"]}: mutates Python but is checked by " \
                        "#{command.empty? ? "the default Ruby command" : command}"
      end
    end
  end
end
