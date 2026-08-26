# frozen_string_literal: true

module Walmart
  # The record of why one sync run stopped, and the questions asked of it.
  #
  # Settlement used to be membership in a list of reasons. It is a property of
  # the whole note. "cutoff" means "we reached the bottom of the window we were
  # given", which is a success only if there was a window; with no lower bound
  # the same word describes a walk that stopped somewhere arbitrary, and that
  # was classified as settled, exited 0, and printed nothing at all.
  #
  # The reason and the window can contradict each other. Today's worker will not
  # send cutoff without a since, but the protocol is directly invocable and a
  # future worker bug would be waved through by a reason-only test. An
  # incoherent note is not settled: we do not know what happened, which is
  # exactly what UNVERIFIED is for.
  #
  # One object answers this for both the archive and the exit code, because
  # those two disagreeing is the contradiction we already fixed once -- a sync
  # exiting 0 while the next read called the same run a truncation.
  class SyncNote
    COMPLETE = "complete"
    CUTOFF = "cutoff"

    # Reasons that can settle, given a coherent note. Membership is necessary
    # and not sufficient; ask settled?, not this.
    SETTLED_REASONS = [COMPLETE, CUTOFF].freeze

    attr_reader :reason, :since, :before

    def initialize(reason:, since: nil, before: nil)
      @reason = reason.to_s
      @since = presence(since)
      @before = presence(before)
    end

    def self.from(state)
      state ||= {}
      new(reason: state["reason"], since: state["since"], before: state["before"])
    end

    # Asked for part of history rather than all of it.
    def windowed? = !(since.nil? && before.nil?)

    # Could this note have been produced by a correct run?
    #
    # An upper bound without a lower one is not a window this CLI ever builds;
    # an empty or inverted interval cannot contain an order; and a cutoff needs
    # something to have been cut off at.
    def coherent?
      return false if before && since.nil?
      return false if before && since && before <= since
      return false if reason == CUTOFF && since.nil?

      true
    end

    # Reached the end of what was asked for.
    def settled? = coherent? && SETTLED_REASONS.include?(reason)

    # Reached the end of *everything*. Requires no window at all: the worker's
    # "complete" only means there was no next page, and a windowed walk skips
    # rows on the way down while still ending that way.
    def all_history? = settled? && reason == COMPLETE && !windowed?

    private

    def presence(value)
      text = value.to_s
      text.empty? ? nil : text
    end
  end
end
