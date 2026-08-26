# frozen_string_literal: true

require "test_helper"

module Walmart
  # Settlement is a property of the whole note, not of the reason alone.
  class SyncNoteTest < TestCase
    def note(**kwargs) = SyncNote.new(reason: "complete", **kwargs)

    def test_a_finished_unwindowed_walk_is_all_of_history
      assert_predicate note(reason: "complete"), :all_history?
      assert_predicate note(reason: "complete"), :settled?
    end

    def test_a_finished_windowed_walk_is_settled_but_not_all_of_history
      bounded = note(reason: "complete", since: "2024-01-01", before: "2025-01-01")

      assert_predicate bounded, :settled?
      refute_predicate bounded, :all_history?
    end

    def test_a_cutoff_inside_a_window_is_settled
      assert_predicate note(reason: "cutoff", since: "2024-01-01"), :settled?
    end

    # The gap this class was written for. "cutoff" means "we reached the bottom
    # of the window we were given"; with no window the same word describes a
    # walk that stopped somewhere arbitrary. It was settled by reason alone, so
    # it exited 0 and every later read stayed silent about a partial archive.
    def test_a_cutoff_with_no_window_is_not_settled
      orphan = note(reason: "cutoff")

      refute_predicate orphan, :coherent?
      refute_predicate orphan, :settled?
      refute_predicate orphan, :all_history?
    end

    def test_an_upper_bound_with_no_lower_bound_is_incoherent
      refute_predicate note(reason: "complete", before: "2025-01-01"), :coherent?
    end

    def test_an_inverted_window_is_incoherent
      refute_predicate note(reason: "complete", since: "2025-01-01", before: "2024-01-01"), :coherent?
    end

    def test_an_empty_window_is_incoherent
      refute_predicate note(reason: "complete", since: "2025-01-01", before: "2025-01-01"), :coherent?
    end

    def test_a_one_day_window_is_coherent
      assert_predicate note(reason: "complete", since: "2025-01-01", before: "2025-01-02"), :coherent?
    end

    # Not settled means UNVERIFIED, which is the honest answer: we cannot tell
    # what happened from a note that contradicts itself.
    def test_an_incoherent_note_is_never_settled
      %w[complete cutoff].each do |reason|
        refute_predicate note(reason: reason, before: "2025-01-01"), :settled?
      end
    end

    def test_a_truncated_walk_is_not_settled_however_coherent
      %w[page_limit rate_limited challenge_detected no_next_page].each do |reason|
        refute_predicate note(reason: reason, since: "2024-01-01"), :settled?
      end
    end

    def test_blank_bounds_read_as_absent
      assert_predicate note(reason: "complete", since: "", before: nil), :all_history?
      refute_predicate note(reason: "complete", since: ""), :windowed?
    end

    def test_it_reads_a_stored_state_hash
      built = SyncNote.from("reason" => "cutoff", "since" => "2024-01-01")

      assert_predicate built, :settled?
      assert_equal "2024-01-01", built.since
    end

    def test_a_missing_state_is_not_settled
      refute_predicate SyncNote.from(nil), :settled?
    end
  end
end
