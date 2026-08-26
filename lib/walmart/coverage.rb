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
        # Scoped completeness. A --year sync that reaches its window is not
        # complete history, but it is not a truncation either, and a script
        # that cannot tell the two apart will re-run it forever.
        "window_complete" => archive.window_complete?,
        "requested_since" => archive.requested_since,
        "last_sync" => state["at"],
        "stopped_because" => archive.synced? ? state["reason"] : NEVER_SYNCED,
        "oldest_seen" => state["oldest_date"],
        # Records the index claims and cannot produce. A machine consumer needs
        # this to tell a thin archive from a damaged one.
        "damaged" => archive.damaged_ids.length
      }.compact
    end

    # The sentence to print, or nil when there is nothing to warn about.
    #
    # Returned rather than printed so the caller owns the output stream; a
    # value object that writes to stderr is a value object you cannot test
    # twice.
    def message
      return never_synced_message unless archive.synced?
      return window_message if archive.sync_state["reason"] == "cutoff"
      return nil unless archive.incomplete?

      state = archive.sync_state
      window = state["oldest_date"] ? " (nothing older than #{day(state["oldest_date"])})" : ""
      "the archive is incomplete#{window}: the last sync stopped early " \
        "(#{state["reason"]}). #{advice_for(state["reason"])}"
    end

    private

    # A cutoff is not a warning. The walk did what it was asked and stopped at
    # the edge of the requested window, so the sentence states the scope and
    # names the only command that widens it. It must not say "run sync again":
    # the same command reaches the same cutoff every time, which is the false
    # remedy we removed from the page ceiling.
    def window_message
      state = archive.sync_state
      window = state["since"] ? " back to #{day(state["since"])}" : ""
      "the archive covers the window that was requested#{window}; " \
        "older history was not asked for. Run `walmart order sync --all` to read everything " \
        "Walmart still shows."
    end

    # What to do next depends on what stopped the walk, and for two reasons the
    # generic "run sync again" is actively wrong. A challenge is cleared by a
    # human in a browser, and running sync again unattended is the traffic
    # pattern that drew it. Throttling wants a wait, not a retry -- this tool
    # never retries a block automatically and should not suggest a person do it
    # immediately either.
    def advice_for(reason)
      case reason
      when "challenge_detected"
        "Walmart asked for a human: run `walmart login`, clear it, then sync again."
      when "rate_limited"
        "Walmart is throttling this session; wait a few minutes, then sync again."
      else
        "Run `walmart order sync` to fill it in."
      end
    end

    # Records can exist without a sync note -- a rebuilt index after a corrupt
    # one, for instance -- so this is not covered by the empty-archive case.
    def never_synced_message
      "nothing has been synced yet, so this is not a complete answer. " \
        "Run `walmart order sync` first."
    end

    def day(value) = value.to_s[0, 10]
  end
end
