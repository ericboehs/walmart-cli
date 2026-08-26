# frozen_string_literal: true

require_relative "../../test_helper"

module Walmart
  module Commands
    class OrderTest < TestCase
      RECORD = {
        "order_id" => "200012345678901",
        "display_id" => "2000123-45678",
        "date" => "2026-08-20T14:02:00.000Z",
        "type" => "delivery",
        "item_count" => 2,
        "totals" => { "grand_total" => 44.32 },
        "items" => [{ "item_id" => "10450114", "name" => "Great Value Whole Milk, 1 Gallon" }]
      }.freeze

      OLDER = RECORD.merge("order_id" => "100000000000001", "date" => "2025-02-03T10:00:00.000Z").freeze

      # --- Archiving --------------------------------------------------------------

      def test_orders_are_written_to_the_archive
        run_sync(orders: [RECORD])

        assert_equal "delivery", Archive.new(paths: paths).record("200012345678901")["type"]
      end

      def test_the_count_of_new_orders_is_reported
        run_sync(orders: [RECORD, OLDER])

        assert_includes out.string, "archived 2 orders"
      end

      def test_a_second_sync_archives_nothing_new
        run_sync(orders: [RECORD])
        run_sync(orders: [RECORD])

        assert_includes out.string, "1 already archived"
      end

      # The expensive part of a sync is the detail page, so the worker is told
      # what we already hold and skips those.
      def test_known_ids_are_sent_to_the_worker
        run_sync(orders: [RECORD])
        worker = sync_worker([])
        run_sync(worker: worker)

        assert_equal ["200012345678901"], worker.calls.last[:params]["known_ids"]
      end

      def test_full_asks_for_everything_again
        run_sync(orders: [RECORD])
        worker = sync_worker([RECORD])
        run_sync(worker: worker, args: ["--full"])

        assert_empty worker.calls.last[:params]["known_ids"]
        assert worker.calls.last[:params]["full"]
      end

      # --- The window --------------------------------------------------------------

      def test_the_default_window_is_two_years
        worker = sync_worker([])
        run_sync(worker: worker)

        expected = (Date.today << 24).iso8601

        assert_equal expected, worker.calls.last[:params]["since"]
      end

      def test_all_asks_for_every_year_walmart_still_shows
        worker = sync_worker([])
        run_sync(worker: worker, args: ["--all"])

        assert_nil worker.calls.last[:params]["since"]
      end

      def test_year_narrows_to_one_calendar_year
        worker = sync_worker([])
        run_sync(worker: worker, args: ["--year", "2025"])

        assert_equal "2025-01-01", worker.calls.last[:params]["since"]
      end

      def test_a_nonsense_year_is_a_usage_error
        assert_raises(UsageError) { run_sync(args: ["--year", "twenty"]) }
      end

      # --- Stopping early ------------------------------------------------------------

      def test_a_complete_walk_exits_clean
        code = run_sync(orders: [RECORD], stopped: { "reason" => "complete" })

        assert_equal ExitCodes::OK, code
        refute_includes err.string, "stopped early"
      end

      def test_reaching_the_cutoff_is_also_clean
        code = run_sync(orders: [RECORD], stopped: { "reason" => "cutoff" })

        assert_equal ExitCodes::OK, code
      end

      # An interrupted walk keeps its records -- they are worth having -- but a
      # script must be able to tell that history is missing.
      def test_a_blocked_walk_keeps_its_records_and_says_so
        code = run_sync(orders: [RECORD], stopped: { "reason" => "challenge_detected" })

        assert_equal ExitCodes::UNVERIFIED, code
        assert_includes err.string, "walmart login"
        assert_equal "delivery", Archive.new(paths: paths).record("200012345678901")["type"]
      end

      def test_a_throttled_walk_advises_waiting
        run_sync(orders: [], stopped: { "reason" => "rate_limited" })

        assert_includes err.string, "throttling"
      end

      def test_an_unfamiliar_stop_reason_is_still_reported_as_incomplete
        code = run_sync(orders: [], stopped: { "reason" => "history_unreadable" })

        assert_equal ExitCodes::UNVERIFIED, code
        assert_includes err.string, "history_unreadable"
      end

      # --- Sync state ------------------------------------------------------------------

      def test_an_interrupted_walk_leaves_the_archive_resumable
        run_sync(orders: [RECORD], stopped: { "reason" => "rate_limited", "cursor" => "CURSOR" })

        archive = Archive.new(paths: paths)

        assert_predicate archive, :resumable?
        assert_equal "CURSOR", archive.sync_state["cursor"]
      end

      def test_a_complete_walk_leaves_the_archive_settled
        run_sync(orders: [RECORD], stopped: { "reason" => "complete" })

        refute_predicate Archive.new(paths: paths), :resumable?
      end

      def test_json_output_reports_the_stop_reason
        run_sync(orders: [RECORD], stopped: { "reason" => "page_limit" }, json: true)

        assert_equal "page_limit", stdout_json["stopped"]["reason"]
        assert_equal 1, stdout_json["archived"]
      end

      def test_an_unknown_subcommand_is_a_usage_error
        assert_raises(UsageError) { run_sync(path: %w[order purge]) }
      end

      private

      def sync_worker(orders, stopped: { "reason" => "complete" }, seen: nil)
        FakeWorker.new(
          results: {
            "order.sync" => {
              "orders" => orders,
              "seen" => seen || orders.map { |order| { "order_id" => order["order_id"] } },
              "stopped" => stopped
            }
          }
        )
      end

      def run_sync(worker: nil, orders: [], stopped: { "reason" => "complete" }, seen: nil,
                   json: false, path: %w[order sync], args: [])
        worker ||= sync_worker(orders, stopped: stopped, seen: seen)
        Order.new(build_context(worker: worker, json: json)).run(path, args)
      end
    end
  end
end
