# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    class OrderReadTest < TestCase
      MILK = {
        "item_id" => "10450114",
        "name" => "Great Value Whole Milk, 1 Gallon",
        "quantity" => 1,
        "quantity_display" => "1",
        "price" => 3.12
      }.freeze

      BANANAS = {
        "item_id" => "44390948",
        "name" => "Fresh Banana, Each",
        "quantity" => 2.5,
        "quantity_display" => "2.5 lb",
        "price" => 1.42
      }.freeze

      RECENT = {
        "order_id" => "200012345678901",
        "display_id" => "2000123-45678",
        "date" => "2026-08-20T14:02:00.000Z",
        "type" => "delivery",
        "item_count" => 2,
        "totals" => { "subtotal" => 40.10, "grand_total" => 44.32 },
        "items" => [MILK, BANANAS]
      }.freeze

      OLDER = {
        "order_id" => "100000000000001",
        "display_id" => "1000000-00001",
        "date" => "2025-02-03T10:00:00.000Z",
        "type" => "pickup",
        "item_count" => 1,
        "totals" => { "grand_total" => 12.00 },
        "items" => [MILK.merge("price" => 2.98)]
      }.freeze

      # --- list ------------------------------------------------------------------

      def test_orders_are_listed_newest_first
        archive_orders(OLDER, RECENT)
        run_read(%w[order list])

        assert_match(/2026-08-20.*\n.*2025-02-03/m, out.string)
      end

      def test_the_list_shows_what_an_order_cost
        archive_orders(RECENT)
        run_read(%w[order list])

        assert_includes out.string, "$44.32"
      end

      def test_a_year_narrows_the_list
        archive_orders(OLDER, RECENT)
        run_read(%w[order list], args: ["--year", "2025"])

        assert_includes out.string, "100000000000001"
        refute_includes out.string, "200012345678901"
      end

      def test_a_nonsense_year_is_a_usage_error
        archive_orders(RECENT)

        assert_raises(UsageError) { run_read(%w[order list], args: ["--year", "recent"]) }
      end

      def test_an_empty_archive_says_how_to_fill_it
        run_read(%w[order list])

        assert_includes err.string, "walmart order sync"
      end

      # Observed, not imagined. An accidental live run drew a challenge before
      # reading a single order, leaving an archive with no entries and a stop
      # note -- and the tool said "nothing is archived yet, run sync first".
      # Sync had run. Walmart blocked it. Running it again unattended is the
      # traffic pattern that drew the block, so that advice was worse than
      # unhelpful. Empty and never-synced are different states.
      def test_an_empty_archive_that_was_blocked_says_so_instead_of_run_sync_first
        archive_orders(sync: { cursor: nil, reason: "challenge_detected", oldest_date: nil })
        run_read(%w[order list])

        assert_includes err.string, "challenge_detected"
        assert_includes err.string, "walmart login"
        refute_includes err.string, "run `walmart order sync` first"
      end

      def test_a_throttled_empty_archive_advises_waiting_rather_than_retrying
        archive_orders(sync: { cursor: nil, reason: "rate_limited", oldest_date: nil })
        run_read(%w[order list])

        assert_includes err.string, "wait a few minutes"
      end

      # The ordinary stop keeps the ordinary advice.
      def test_an_empty_archive_stopped_at_the_page_ceiling_still_says_run_sync
        archive_orders(sync: { cursor: "abc", reason: "page_limit", oldest_date: nil })
        run_read(%w[order list])

        assert_includes err.string, "walmart order sync"
      end

      # --- show ------------------------------------------------------------------

      def test_an_order_shows_its_items
        archive_orders(RECENT)
        run_read(%w[order show], args: ["200012345678901"])

        assert_includes out.string, "Great Value Whole Milk"
        assert_includes out.string, "44390948"
      end

      # Rebuilding "2.5 lb" from a number and a unit produces "2.5 EACH".
      def test_walmarts_own_quantity_label_is_shown
        archive_orders(RECENT)
        run_read(%w[order show], args: ["200012345678901"])

        assert_includes out.string, "2.5 lb"
      end

      def test_totals_come_from_the_record
        archive_orders(RECENT)
        run_read(%w[order show], args: ["200012345678901"])

        assert_includes out.string, "grand total: $44.32"
      end

      # A total derived by summing the lines misses fees, tax and weight
      # adjustments, and printing it as if it were real is worse than nothing.
      def test_an_order_without_totals_says_so_instead_of_summing_the_lines
        archive_orders(RECENT.merge("totals" => {}))
        run_read(%w[order show], args: ["200012345678901"])

        assert_includes out.string, "archived without totals"
        refute_includes out.string, "$4.54"
      end

      def test_an_order_is_findable_by_its_display_id
        archive_orders(RECENT)
        run_read(%w[order show], args: ["2000123-45678"])

        assert_includes out.string, "Great Value Whole Milk"
      end

      # Nobody wants to type fifteen digits.
      def test_an_unambiguous_tail_is_enough
        archive_orders(RECENT, OLDER)
        run_read(%w[order show], args: ["678901"])

        assert_includes out.string, "Great Value Whole Milk"
      end

      def test_an_ambiguous_fragment_is_refused_rather_than_guessed
        archive_orders(RECENT, OLDER.merge("order_id" => "200012345678902", "display_id" => "x"))

        error = assert_raises(UsageError) { run_read(%w[order show], args: ["20001234567890"]) }
        assert_includes error.message, "2 archived orders match"
      end

      def test_a_missing_order_suggests_a_sync
        archive_orders(RECENT)

        error = assert_raises(Error) { run_read(%w[order show], args: ["999"]) }
        assert_includes error.message, "order sync"
      end

      # "The index lists it but the file is gone" is not "no such order": a
      # dedupe-by-id sync will not re-fetch it, so saying the wrong thing sends
      # someone in a circle.
      def test_a_missing_file_is_distinguished_from_a_missing_order
        archive_orders(RECENT)
        FileUtils.rm_rf(File.join(paths.orders_dir, "2026"))

        error = assert_raises(Error) { run_read(%w[order show], args: ["200012345678901"]) }
        assert_includes error.message, "file is missing"
      end

      def test_show_needs_an_order
        assert_raises(UsageError) { run_read(%w[order show], args: []) }
      end

      # --- search ------------------------------------------------------------------

      def test_search_finds_an_item_across_orders
        archive_orders(RECENT, OLDER)
        run_read(%w[order search], args: ["milk"])

        assert_includes out.string, "Great Value Whole Milk"
      end

      def test_search_counts_how_often_something_was_bought
        archive_orders(RECENT, OLDER)
        run_read(%w[order search], args: ["milk"], json: true)

        assert_equal 2, stdout_json["results"].first["times"]
      end

      # Walmart renames things. Grouping by title would report one product as
      # two and make "we buy this every week" look like two rare purchases.
      def test_items_are_grouped_by_id_not_by_title
        renamed = OLDER.merge("items" => [MILK.merge("name" => "Great Value Whole Vitamin D Milk")])
        archive_orders(RECENT, renamed)
        run_read(%w[order search], args: ["milk"], json: true)

        assert_equal 1, stdout_json["results"].length
        assert_equal 2, stdout_json["results"].first["times"]
      end

      def test_the_most_recent_name_and_price_are_the_ones_shown
        archive_orders(RECENT, OLDER)
        run_read(%w[order search], args: ["milk"], json: true)

        assert_in_delta 3.12, stdout_json["results"].first["price"]
      end

      # The staple has to come first. This is the list someone scans to answer
      # "what do we always get", and a one-off above a weekly buy makes it
      # useless.
      def test_the_most_bought_item_ranks_first
        staple = { "item_id" => "1", "name" => "Milk staple", "price" => 3.12 }
        treat = { "item_id" => "2", "name" => "Milk treat", "price" => 9.99 }
        archive_orders(
          RECENT.merge("items" => [staple, treat]),
          OLDER.merge("items" => [staple]),
          OLDER.merge("order_id" => "100000000000002", "display_id" => "b", "items" => [staple])
        )
        run_read(%w[order search], args: ["milk"], json: true)

        ranked = stdout_json["results"].map { |row| row["item_id"] }

        assert_equal %w[1 2], ranked
      end

      # Within a tie, the thing bought most recently is the more useful answer.
      def test_a_tie_is_broken_by_recency
        older_item = { "item_id" => "old", "name" => "Milk older", "price" => 1.0 }
        newer_item = { "item_id" => "new", "name" => "Milk newer", "price" => 1.0 }
        archive_orders(RECENT.merge("items" => [newer_item]), OLDER.merge("items" => [older_item]))
        run_read(%w[order search], args: ["milk"], json: true)

        ranked = stdout_json["results"].map { |row| row["item_id"] }

        assert_equal %w[new old], ranked
      end

      def test_search_needs_a_term
        assert_raises(UsageError) { run_read(%w[order search], args: []) }
      end

      def test_a_search_with_no_hits_says_so
        archive_orders(RECENT)
        run_read(%w[order search], args: ["saffron"])

        assert_includes out.string, "No matches"
      end

      # --- coverage --------------------------------------------------------------

      # "We have never bought that" and "we have not looked that far back" are
      # different facts, and only one of them is a reason to stop looking.
      def test_a_partial_archive_says_so_when_listing
        archive_orders(RECENT, sync: { reason: "rate_limited", cursor: "CURSOR" })
        run_read(%w[order list])

        assert_includes err.string, "archive is incomplete"
      end

      def test_a_partial_archive_says_so_when_searching
        archive_orders(RECENT, sync: { reason: "challenge_detected", cursor: "CURSOR" })
        run_read(%w[order search], args: ["saffron"])

        assert_includes err.string, "archive is incomplete"
      end

      def test_a_complete_archive_stays_quiet
        archive_orders(RECENT, sync: { reason: "complete" })
        run_read(%w[order list])

        refute_includes err.string, "incomplete"
      end

      def test_json_carries_the_coverage_alongside_the_results
        archive_orders(RECENT, sync: { reason: "page_limit", cursor: "CURSOR" })
        run_read(%w[order list], json: true)

        assert_equal false, stdout_json["coverage"]["complete"]
        assert_equal "page_limit", stdout_json["coverage"]["stopped_because"]
      end

      # A never-synced archive is not a complete one. A machine consumer asking
      # "is this the whole history" must not be told yes when the true answer is
      # "we have never looked".
      def test_a_never_synced_archive_is_not_reported_complete
        archive_orders(RECENT)
        run_read(%w[order list], json: true)

        assert_equal false, stdout_json["coverage"]["complete"]
        assert_equal false, stdout_json["coverage"]["synced"]
        assert_equal "never_synced", stdout_json["coverage"]["stopped_because"]
      end

      # Records can exist without a sync note -- a rebuilt index, for instance --
      # so the empty-archive message does not cover this.
      def test_a_never_synced_archive_says_so_in_text_mode
        archive_orders(RECENT)
        run_read(%w[order list])

        assert_includes err.string, "nothing has been synced yet"
      end

      def test_a_synced_complete_archive_reports_complete
        archive_orders(RECENT, sync: { reason: "complete" })
        run_read(%w[order list], json: true)

        assert_equal true, stdout_json["coverage"]["complete"]
      end

      # --- The --json contract ------------------------------------------------------

      # Zero bytes and exit 0 is indistinguishable from a successful empty
      # result only if you already know the contract is broken.
      def test_an_empty_search_is_still_a_json_document
        archive_orders(RECENT, sync: { reason: "complete" })
        run_read(%w[order search], args: ["saffron"], json: true)

        assert_empty stdout_json["results"]
      end

      def test_an_empty_list_is_still_a_json_document
        run_read(%w[order list], json: true)

        assert_empty stdout_json["results"]
      end

      # The empty document has to carry the coverage too, or a consumer reads
      # "no results" from an archive that was never filled and believes it.
      def test_the_empty_document_still_reports_coverage
        run_read(%w[order search], args: ["milk"], json: true)

        assert_equal false, stdout_json["coverage"]["complete"]
      end

      def test_an_unknown_subcommand_is_a_usage_error
        assert_raises(UsageError) { run_read(%w[order purge]) }
      end

      # --- offline ------------------------------------------------------------------

      # These three answer from disk. Reaching for a browser to list what we
      # already have would make the archive pointless.
      def test_reads_never_start_a_worker
        archive_orders(RECENT)
        worker = FakeWorker.new(results: {})
        run_read(%w[order list], worker: worker)
        run_read(%w[order show], args: ["200012345678901"], worker: worker)
        run_read(%w[order search], args: ["milk"], worker: worker)

        assert_empty worker.calls
      end

      private

      def archive_orders(*orders, sync: nil)
        archive = Archive.new(paths: paths)
        orders.each { |order| archive.write(order) }
        archive.note_sync(**sync) if sync
      end

      def run_read(path, args: [], json: false, worker: nil)
        worker ||= FakeWorker.new(results: {})
        OrderRead.new(build_context(worker: worker, json: json)).run(path, args)
      end
    end
  end
end
