# frozen_string_literal: true

require_relative "../test_helper"
require "open3"

module Walmart
  # script/mutate edits tracked source in place, so the ways it can go wrong are
  # not ordinary bugs: they leave the working tree holding a deliberate defect.
  #
  # This exists because of a mutant that removed the pump from a wait loop. The
  # test watching that loop spun instead of failing, the harness waited for an
  # answer that was never coming, and the run sat there for eighty-seven minutes
  # until it was cancelled by hand. Cancellation is also the one path that skips
  # the ensure, so the unbounded wait made a mutated tree likelier as well.
  #
  # A hang is not a third answer to "does anything notice?". It is a broken
  # test, and the harness has to say so on its own.
  class MutationHarnessTest < TestCase
    ROOT = File.expand_path("../..", __dir__)
    # Long enough that a working timeout of 1s is never a flake, short enough
    # that a broken one fails this test instead of stalling the suite.
    PATIENCE = 20

    # A mutant whose file is this repository's own parity test: chosen because
    # it is tracked, so the harness's dirty-tree and backup paths run for real.
    PLANNED_FILE = "test/walmart/documentation_parity_test.rb"
    LABEL = "the parity file list goes back to being remembered"

    def test_a_command_that_never_answers_is_killed_and_reported
      out, seconds = run_mutate(timeout: "1", command: %w[sleep 60])

      assert_match(/no answer in 1s/, out, "the harness waited instead of reporting")
      assert_operator seconds, :<, PATIENCE
    end

    def test_the_tree_is_not_left_mutated_when_a_command_is_killed
      before = File.read(File.join(ROOT, PLANNED_FILE))
      run_mutate(timeout: "1", command: %w[sleep 60])

      assert_equal before, File.read(File.join(ROOT, PLANNED_FILE)),
                   "a killed run left the mutation on disk"
    end

    private

    # Bounded by this test rather than by the thing under test, so a harness
    # that has lost its timeout fails here instead of inheriting our patience.
    def run_mutate(timeout:, command:)
      argv = ["ruby", "script/mutate", "--plan", "script/mutants.json",
              "--allow-dirty", "--only", LABEL, "--", *command]
      started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
      out = +""

      Open3.popen2e({ "MUTATE_TIMEOUT" => timeout }, *argv, chdir: ROOT) do |stdin, io, thread|
        stdin.close
        reader = Thread.new { out << io.read }
        kill(thread) if thread.join(PATIENCE).nil?
        reader.join
      end

      [out, Process.clock_gettime(Process::CLOCK_MONOTONIC) - started]
    end

    def kill(thread)
      Process.kill("KILL", thread.pid)
      thread.join
      flunk "script/mutate did not return within #{PATIENCE}s; its timeout is not working"
    end
  end
end
