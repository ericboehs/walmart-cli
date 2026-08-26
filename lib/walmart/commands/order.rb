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
    # right response is to keep what arrived, say how far we got, and make the
    # next run resume. The failure this command exists to avoid is an archive
    # that quietly believes it is complete.
    class Order < Base
      DEFAULT_WINDOW_YEARS = 2

      # Reasons that mean nothing is missing from what we asked for.
      SETTLED = %w[complete cutoff].freeze

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
        result = worker.run("order.sync", params: params_for(flags))
        stored = store(Array(result["orders"]), full: flags[:full] == true)
        stopped = result["stopped"] || {}
        archive.note_sync(
          cursor: stopped["cursor"], reason: stopped["reason"].to_s, oldest_date: stopped["oldest_date"]
        )

        report(stored, Array(result["seen"]).length, stopped)
      end

      def params_for(flags)
        {
          "since" => since(flags),
          "full" => flags[:full] == true,
          # Ids we already hold. Sending them is what makes a re-sync cheap:
          # the worker skips their detail pages instead of re-reading them.
          "known_ids" => flags[:full] == true ? [] : archive.index["orders"].keys
        }
      end

      # --year narrows to one calendar year, --all asks for everything Walmart
      # still shows, and the default is a rolling two-year window.
      def since(flags)
        return nil if flags[:all] == true

        if flags[:year]
          year = positive_integer_flag(flags, :year, default: 0)
          raise UsageError, "--year expects a four-digit year" unless year.between?(2000, 2100)

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
          fmt.hint("stopped early at the page ceiling; run sync again to continue from here.")
        else
          fmt.hint("stopped early (#{reason}); history before this point was not read. " \
                   "Run sync again to resume.")
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
