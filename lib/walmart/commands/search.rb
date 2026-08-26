# frozen_string_literal: true

require_relative "base"

module Walmart
  module Commands
    # `walmart search QUERY [--limit N] [--page N] [--marketplace]`
    #
    # Results are first-party by default. Walmart's search mixes marketplace
    # sellers into the same list, and for groceries "will this arrive with my
    # delivery order" matters more than "does this exist". --marketplace opts
    # into the wider list rather than making the narrow one a surprise.
    class Search < Base
      DEFAULT_LIMIT = 20
      NEEDS_A_QUERY = 'search needs something to search for: walmart search "whole milk"'

      COLUMNS = [
        [:index, "#", :right],
        [:price, "price", :right],
        [:unit_price, "unit price", :left],
        [:delivery, "delivery", :left],
        [:seller, "seller", :left],
        [:name, "item", :left]
      ].freeze

      def run(_path, args)
        positional, flags = parse_flags(
          args,
          valued: %w[limit page],
          boolean: %w[marketplace],
          max_positional: nil
        )
        query = positional.join(" ").strip
        raise UsageError, NEEDS_A_QUERY if query.empty?

        data = fetch(query, flags)
        render(query, data, marketplace: flags[:marketplace] == true)
      end

      private

      def fetch(query, flags)
        worker.run(
          "search",
          params: {
            "query" => query,
            "page" => positive_integer_flag(flags, :page, default: 1),
            "limit" => positive_integer_flag(flags, :limit, default: DEFAULT_LIMIT)
          }
        )
      end

      def render(query, data, marketplace:)
        items = Array(data["items"])
        shown = marketplace ? items : items.select { |item| item["first_party"] }

        if json?
          fmt.emit_json(data.merge("query" => query, "items" => shown))
        elsif shown.empty?
          report_empty(query, items, marketplace)
        else
          fmt.table(shown.each_with_index.map { |item, index| row(item, index) }, COLUMNS)
          report_pagination(data, shown.length)
        end

        # An empty catalog is a successful search, not a failed one. Only a
        # refusal or an error is a nonzero exit.
        ExitCodes::OK
      end

      # An empty list has two very different causes, and conflating them sends
      # the user looking in the wrong place. "Walmart has none" is a fact about
      # the catalog; "we filtered them all out" is a fact about our own flag.
      def report_empty(query, items, marketplace)
        if !marketplace && items.any?
          fmt.empty_result(what: "\"#{query}\"", reason: "#{items.length} result(s) are marketplace sellers")
          fmt.hint("add --marketplace to include sellers other than Walmart.com")
        else
          fmt.empty_result(what: "\"#{query}\"")
        end
      end

      def report_pagination(data, shown)
        pagination = data["pagination"] || {}
        return unless pagination["has_more"]

        page = pagination["page"] || 1
        fmt.hint("showing #{shown} of more results; --page #{page.to_i + 1} for the next page")
      end

      def row(item, index)
        {
          index: index + 1,
          price: item["price_display"] || format_price(item["price"]),
          unit_price: item["unit_price_display"],
          delivery: delivery_cell(item),
          seller: item["first_party"] ? "Walmart" : item["seller_name"],
          name: item["name"]
        }
      end

      # A price we could not read must not print as "$" or as 0. An empty cell
      # is honest; a zero is a lie about what something costs.
      def format_price(value)
        return nil if value.nil?

        format("$%.2f", value)
      end

      def delivery_cell(item)
        return "no" unless item["delivery"]
        return "yes" unless item["delivery_date"]

        item["delivery_date"].to_s
      end
    end
  end
end
