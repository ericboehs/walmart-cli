# frozen_string_literal: true

require "date"

require_relative "base"
require_relative "../archive"

module Walmart
  module Commands
    # `walmart order sync [--year YYYY] [--all] [--full]`
    #
    # Archives orders and in-store receipts. Read-only against Walmart: the walk
    # navigates and reads, and everything it changes is on this machine.
    #
    # A sync that stops early is a normal outcome, not an error. Walmart can
    # throttle or challenge us halfway through two years of history, and the
    # right response is to keep what arrived and say how far we got. The failure
    # this command exists to avoid is an archive that quietly believes it is
    # complete.
    #
    # A second sync does not resume; it restarts at page one. Already-archived
    # ids are sent so their detail pages are skipped, which makes a re-sync
    # cheap, but it does not advance the pagination budget. Nothing here should
    # describe the archive as resumable, because nothing here can resume.
    class Order < Base
      DEFAULT_WINDOW_YEARS = 2

      # Mirrors MAX_PAGES in pyworker/orders.py, for the message only. Two
      # copies beat one shared constant here: this is the number a human is
      # told, and if the worker's ceiling moves without this moving, the test
      # that reads both files fails.
      PAGE_CEILING = 200

      # Reasons that mean nothing is missing from what we asked for.
      #
      # Two entries, and both earned it: COMPLETE is the end of history, CUTOFF
      # is the end of the window the user asked for. Every other reason leaves
      # history unread, so widening this list is how a truncated sync starts
      # reporting success -- the exact failure this command exists to avoid.
      # Pinned by a test for that reason; see test_order.rb.
      SETTLED = %w[complete cutoff].freeze

      # Every reason the worker can produce, so a rename on that side fails
      # here instead of silently degrading to the catch-all branch.
      KNOWN_REASONS = %w[
        complete cutoff page_limit no_next_page history_unreadable
        order_unreadable rate_limited challenge_detected
      ].freeze

      def run(path, args)
        subcommand = path[1] || "sync"
        raise UsageError, "unknown order subcommand: #{subcommand}" unless subcommand == "sync"

        positional, flags = parse_flags(args, valued: %w[year], boolean: %w[all full], max_positional: 0)
        raise UsageError, "unexpected argument: #{positional.first}" unless positional.empty?

        sync(flags)
      end

      private

      def archive = @archive ||= Archive.new(paths: context.paths)

      def sync(flags)
        report_damage
        params = params_for(flags)
        result = worker.run("order.sync", params: params)
        stored = store(Array(result["orders"]), full: flags[:full] == true)
        stopped = result["stopped"] || {}
        archive.note_sync(
          reason: stopped["reason"].to_s, oldest_date: stopped["oldest_date"],
          since: params["since"]
        )

        report(stored, Array(result["seen"]).length, stopped)
      end

      # Said before the sync, because the sync is the repair: those ids are
      # absent from known_ids, so the worker reads them again and write/2 now
      # accepts the replacement.
      def report_damage
        damaged = archive.damaged_ids
        return if damaged.empty?

        fmt.warn("#{damaged.length} archived #{damaged.length == 1 ? "order" : "orders"} " \
                 "cannot be read; this sync will fetch #{damaged.length == 1 ? "it" : "them"} again")
      end

      def params_for(flags)
        {
          "since" => since(flags),
          "full" => flags[:full] == true,
          # Ids we already hold *and can return*. Sending them is what makes a
          # re-sync cheap: the worker skips their detail pages. Index keys were
          # used here, which meant an entry left behind by a missing or corrupt
          # file kept the order skipped on every sync, so the damage was
          # permanent unless someone happened to run --full.
          "known_ids" => flags[:full] == true ? [] : archive.readable_ids
        }
      end

      # --year narrows to one calendar year, --all asks for everything Walmart
      # still shows, and the default is a rolling two-year window.
      def since(flags)
        return nil if flags[:all] == true

        if flags[:year]
          # Not positive_integer_flag: --year takes a year, not a count of
          # things, and "a positive whole number of a year" is what borrowing
          # that message produced. One check, one sentence, and the sentence
          # states the actual contract.
          year = flags[:year]
          raise UsageError, "--year expects a four-digit year" if year == true

          unless year.to_s.match?(/\A\d{4}\z/) && year.to_i.between?(2000, 2100)
            raise UsageError, "--year expects a four-digit year between 2000 and 2100 " \
                              "(got #{year.inspect})"
          end

          return "#{year}-01-01"
        end

        (Date.today << (12 * DEFAULT_WINDOW_YEARS)).iso8601
      end

      def store(orders, full:)
        orders.count { |order| archive.write(order, force: full) == :written }
      end

      def report(stored, seen, stopped)
        reason = stopped["reason"].to_s

        if json?
          fmt.emit_json("archived" => stored, "seen" => seen, "stopped" => stopped)
          return exit_code_for(reason)
        end

        fmt.say(summary(stored, seen))
        explain(reason)
        exit_code_for(reason)
      end

      def summary(stored, seen)
        return "No orders found." if seen.zero?

        skipped = seen - stored
        parts = ["archived #{stored} order#{"s" unless stored == 1}"]
        parts << "#{skipped} already archived" if skipped.positive?
        parts.join(", ")
      end

      # An interrupted walk has to say so in words, not only in an exit code.
      # Someone reading a terminal is the last line of defence against an
      # archive that thinks it is whole.
      def explain(reason)
        case reason
        when *SETTLED then nil
        when "challenge_detected"
          fmt.hint("stopped early: Walmart asked for a human. " \
                   "Run `walmart login`, clear it, then sync again.")
        when "rate_limited"
          fmt.hint("stopped early: Walmart is throttling this session. Wait a few minutes, then sync again.")
        when "page_limit"
          # Deliberately does not say "run sync again". A re-run starts at page
          # one and spends the same ceiling reaching the same frontier, so for a
          # history longer than the ceiling it stops in exactly this place every
          # time. Telling someone to repeat that is telling them to wait for a
          # different outcome from the same work.
          fmt.hint("stopped early at the page ceiling (#{Order::PAGE_CEILING} pages); " \
                   "history older than this cannot be reached by re-running.")
        else
          fmt.hint("stopped early (#{reason}); history before this point was not read. " \
                   "Run sync again to re-read from the newest page.")
        end
      end

      # An incomplete archive is not a failed command -- the records that did
      # arrive are on disk and are worth having -- but it is not a clean one
      # either, and a script should be able to tell.
      def exit_code_for(reason)
        SETTLED.include?(reason) ? ExitCodes::OK : ExitCodes::UNVERIFIED
      end
    end
  end
end
