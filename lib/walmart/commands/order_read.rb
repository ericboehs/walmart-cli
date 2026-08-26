# frozen_string_literal: true

require_relative "base"
require_relative "../archive"
require_relative "../coverage"

module Walmart
  module Commands
    # `walmart order list`, `order show`, `order search` -- the archive, read
    # locally. These three never touch the network. Once a sync has run, the
    # question "when did we last buy oat milk" is answerable on a bad
    # connection, on a plane, and without asking Walmart anything.
    #
    # The recurring obligation in here is honesty about coverage. The archive
    # knows whether the last walk finished, and every one of these commands
    # says so when it did not. A search that finds nothing in an archive that
    # only got halfway is not the same answer as a search that finds nothing in
    # a complete one, and presenting them identically would be the tool lying
    # by omission.
    class OrderRead < Base
      LIST_COLUMNS = [
        [:date, "DATE", :left],
        [:order_id, "ORDER", :left],
        [:type, "TYPE", :left],
        [:items, "ITEMS", :right],
        [:total, "TOTAL", :right]
      ].freeze

      SHOW_COLUMNS = [
        [:quantity, "QTY", :right],
        [:name, "ITEM", :left],
        [:item_id, "ITEM ID", :left],
        [:price, "PRICE", :right]
      ].freeze

      SEARCH_COLUMNS = [
        [:times, "TIMES", :right],
        [:last, "LAST", :left],
        [:name, "ITEM", :left],
        [:price, "PRICE", :right]
      ].freeze

      DEFAULT_LIMIT = 20

      def run(path, args)
        case path[1]
        when "list" then list(args)
        when "show" then show(args)
        when "search" then search(args)
        else raise UsageError, "unknown order subcommand: #{path[1]}"
        end
      end

      private

      def archive = @archive ||= Archive.new(paths: context.paths)

      # --- list ---------------------------------------------------------------

      def list(args)
        positional, flags = parse_flags(args, valued: %w[year limit], boolean: %w[all], max_positional: 0)
        raise UsageError, "unexpected argument: #{positional.first}" unless positional.empty?

        rows = filter_by_year(archive.entries, flags[:year])
        rows = rows.first(limit(flags)) unless flags[:all]

        return empty("archived orders") if rows.empty?

        emit(rows.map { |entry| list_row(entry) }, LIST_COLUMNS, rows)
      end

      def filter_by_year(entries, year)
        return entries unless year

        raise UsageError, "--year expects a four-digit year" unless year.to_s.match?(/\A\d{4}\z/)

        entries.select { |entry| entry["date"].to_s.start_with?(year.to_s) }
      end

      def limit(flags) = positive_integer_flag(flags, :limit, unit: "orders", default: DEFAULT_LIMIT)

      def list_row(entry)
        {
          date: day(entry["date"]),
          order_id: entry["order_id"],
          type: entry["type"],
          items: entry["item_count"],
          total: money(entry["total"])
        }
      end

      # --- show ---------------------------------------------------------------

      def show(args)
        positional, = parse_flags(args, max_positional: 1)
        raise UsageError, "walmart order show needs an order id" if positional.empty?

        # Damage raises ArchiveError from the archive itself, naming the order
        # and the reason; nil now means only that we have never archived it.
        record = archive.record(resolve(positional.first))
        raise Error.new("no archived order #{positional.first}", code: "record_missing") if record.nil?

        render_order(record)
      end

      # Accepts the Walmart order id, the display id, or an unambiguous tail of
      # either -- nobody wants to type fifteen digits, and refusing a prefix
      # invites copy-paste of the wrong field.
      def resolve(query)
        text = query.to_s.strip
        entries = archive.entries
        exact = entries.find { |entry| entry["order_id"] == text || entry["display_id"] == text }
        return exact["order_id"] if exact

        matches = entries.select { |entry| matches_partial?(entry, text) }
        raise Error.new(not_found(text), code: "not_found") if matches.empty?
        raise UsageError, ambiguous(text, matches) if matches.length > 1

        matches.first["order_id"]
      end

      def matches_partial?(entry, text)
        %w[order_id display_id].any? { |key| entry[key].to_s.include?(text) }
      end

      def not_found(text)
        "no archived order matching #{text}; run `walmart order sync` if it is recent"
      end

      def ambiguous(text, matches)
        ids = matches.first(5).map { |entry| entry["order_id"] }.join(", ")
        "#{matches.length} archived orders match #{text}: #{ids}"
      end

      def render_order(record)
        return emit_record(record) if json?

        fmt.say(header(record))
        fmt.say
        fmt.table(record["items"].to_a.map { |item| item_row(item) }, SHOW_COLUMNS)
        fmt.say
        totals(record).each { |line| fmt.say(line) }
        coverage_notice
        ExitCodes::OK
      end

      def header(record)
        parts = [day(record["date"]), record["type"], record["display_id"] || record["order_id"]]
        parts.compact.join("  ")
      end

      def item_row(item)
        {
          # Walmart's own quantity label is kept: rebuilding "2.5 lb" from a
          # number and a unit produces "2.5 EACH".
          quantity: item["quantity_display"] || item["quantity"],
          name: item["name"],
          item_id: item["item_id"],
          price: money(item["price"])
        }
      end

      # Totals come from Walmart or not at all. A total derived by summing the
      # lines is not the total that was charged -- it misses fees, discounts,
      # tax and weight adjustments -- and printing it as if it were is worse
      # than printing nothing.
      def totals(record)
        totals = record["totals"] || {}
        return ["(this order was archived without totals)"] if totals.empty?

        totals.filter_map do |key, value|
          "#{key.to_s.tr("_", " ")}: #{money(value)}" unless value.nil?
        end
      end

      # --- search --------------------------------------------------------------

      def search(args)
        positional, flags = parse_flags(args, valued: %w[limit], boolean: %w[all], max_positional: nil)
        raise UsageError, "walmart order search needs something to search for" if positional.empty?

        query = positional.join(" ").downcase
        found = rank(matching_items(query))
        found = found.first(limit(flags)) unless flags[:all]

        return empty("#{query} in archived orders") if found.empty?

        emit(found.map { |row| search_row(row) }, SEARCH_COLUMNS, found)
      end

      # Items are grouped by usItemId, never by title. Walmart renames things
      # -- "Great Value Whole Milk" becomes "Great Value Whole Vitamin D Milk"
      # -- and grouping by name would report one product as two.
      def matching_items(query)
        archive.entries.each_with_object({}) do |entry, found|
          # readable_record: a search across an intact archive must not die on
          # one damaged file. The count is reported separately so the gap is
          # visible rather than silent.
          record = archive.readable_record(entry["order_id"])
          next unless record

          record["items"].to_a.each do |item|
            next unless item["name"].to_s.downcase.include?(query)

            merge_item(found, item, record)
          end
        end
      end

      def merge_item(found, item, record)
        key = item["item_id"] || item["name"]
        row = found[key] ||= { "item_id" => item["item_id"], "times" => 0 }
        row["times"] += 1
        # Newest first, so the first sighting is the most recent one: that is
        # the name and price worth showing.
        row["name"] ||= item["name"]
        row["price"] ||= item["price"]
        row["last"] ||= record["date"]
        row["orders"] ||= []
        row["orders"] << record["order_id"]
      end

      # Most-bought first, and most-recent first within a tie. Written as one
      # comparison rather than a sort plus a reverse, because reversing a
      # multi-key sort inverts every key: it put the thing bought once above
      # the thing bought weekly, which is exactly backwards for a list whose
      # whole purpose is "what do we always get".
      def rank(found)
        found.values.sort do |left, right|
          [right["times"], right["last"].to_s] <=> [left["times"], left["last"].to_s]
        end
      end

      def search_row(row)
        {
          times: row["times"],
          last: day(row["last"]),
          name: row["name"],
          price: money(row["price"])
        }
      end

      # --- shared ---------------------------------------------------------------

      def emit(rows, columns, records)
        return emit_record(records) if json?

        fmt.table(rows, columns)
        coverage_notice
        ExitCodes::OK
      end

      def emit_record(payload)
        fmt.emit_json(payload.is_a?(Array) ? { "results" => payload, "coverage" => coverage.to_h } : payload)
        ExitCodes::OK
      end

      # An empty result is still a result, and in JSON mode it must still be a
      # document: a consumer piping stdout to a parser gets a parse error from
      # zero bytes, and the exit code says success, so the failure surfaces
      # somewhere the script author never looked.
      #
      # "Empty" and "never synced" are not the same state, and this branch used
      # to treat them as one. A sync that Walmart blocked before it read a
      # single order leaves an archive with no entries and a stop note, and the
      # advice for that is not "run sync first" -- it is whatever cleared the
      # thing that stopped it. Observed, not imagined: an accidental live run
      # left exactly this state, and the tool said "nothing is archived yet".
      def empty(what)
        return emit_record([]) if json?

        if archive.synced?
          fmt.empty_result(what: what, where: "the archive",
                           reason: "the last sync did not read any orders")
          coverage_notice
        else
          fmt.empty_result(what: what, where: "the archive", reason: "nothing is archived yet")
          fmt.hint("run `walmart order sync` first")
        end
        ExitCodes::OK
      end

      # An answer from a partial archive has to say it is partial. The claim
      # itself lives in Coverage, which both output modes read, so the JSON
      # field and the printed sentence cannot drift apart.
      def coverage = @coverage ||= Coverage.new(archive)

      # Integrity is reported after coverage and independently of it. A scoped
      # window sentence used to satisfy the "did we say something" question
      # while leaving unreadable records unmentioned, so the two are no longer
      # in the same branch: one says how much history was asked for, the other
      # how much of what we hold can actually be produced.
      def coverage_notice
        message = coverage.message
        fmt.hint(message) if message
        damage_notice
        incompatible_notice
      end

      # Skipping a damaged record silently would make an archive look thin when
      # it is actually broken, and those need different responses: one is
      # "sync more", the other is "sync repairs this".
      def damage_notice
        damaged = coverage.integrity.damaged
        return if damaged.empty?

        fmt.warn("#{damaged.length} archived #{damaged.length == 1 ? "order" : "orders"} " \
                 "cannot be read and #{damaged.length == 1 ? "was" : "were"} left out")
        fmt.hint("run: walmart order sync    (it will fetch #{damaged.length == 1 ? "it" : "them"} again)")
      end

      # Never advises a sync. Syncing cannot repair these and deliberately will
      # not try: they are protected from being overwritten, which is the point.
      def incompatible_notice
        newer = coverage.integrity.incompatible
        return if newer.empty?

        fmt.warn("#{newer.length} archived #{newer.length == 1 ? "order was" : "orders were"} " \
                 "written by a newer walmart-cli and cannot be read here")
        fmt.hint("upgrade walmart-cli to read #{newer.length == 1 ? "it" : "them"}")
      end

      def day(value) = value.to_s[0, 10]

      def money(value)
        return nil if value.nil?

        format("$%.2f", value)
      end
    end
  end
end
