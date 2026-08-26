# frozen_string_literal: true

module Walmart
  # What we can honestly say about how much history is behind an answer.
  #
  # This exists as its own object because it is a claim, not a formatting
  # detail. Every archive read makes it -- in JSON as a field a script will act
  # on, in a terminal as a sentence a person will act on -- and the two must
  # not be able to drift apart.
  #
  # Three states, deliberately not two:
  #
  #   never synced  we have never looked
  #   incomplete    we looked and stopped early
  #   complete      we looked and reached the end
  #
  # Collapsing the first into the last is the failure this whole layer exists
  # to prevent: a consumer asks "is this the whole history" and gets yes, when
  # the true answer is "nobody has ever checked".
  class Coverage
    NEVER_SYNCED = "never_synced"

    def initialize(archive)
      @archive = archive
    end

    attr_reader :archive

    def to_h
      state = archive.sync_state
      {
        "synced" => archive.synced?,
        "complete" => archive.complete?,
        "last_sync" => state["at"],
        "stopped_because" => archive.synced? ? state["reason"] : NEVER_SYNCED,
        "oldest_seen" => state["oldest_date"]
      }.compact
    end

    # The sentence to print, or nil when there is nothing to warn about.
    #
    # Returned rather than printed so the caller owns the output stream; a
    # value object that writes to stderr is a value object you cannot test
    # twice.
    def message
      return never_synced_message unless archive.synced?
      return nil unless archive.resumable?

      state = archive.sync_state
      window = state["oldest_date"] ? " (nothing older than #{day(state["oldest_date"])})" : ""
      "the archive is incomplete#{window}: the last sync stopped early (#{state["reason"]}). " \
        "Run `walmart order sync` to fill it in."
    end

    private

    # Records can exist without a sync note -- a rebuilt index after a corrupt
    # one, for instance -- so this is not covered by the empty-archive case.
    def never_synced_message
      "nothing has been synced yet, so this is not a complete answer. " \
        "Run `walmart order sync` first."
    end

    def day(value) = value.to_s[0, 10]
  end
end
