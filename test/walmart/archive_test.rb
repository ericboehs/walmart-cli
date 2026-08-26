# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  class ArchiveTest < TestCase
    ORDER = {
      "order_id" => "200012345678901",
      "display_id" => "2000123-45678",
      "date" => "2026-08-20T14:02:00.000Z",
      "type" => "delivery",
      "item_count" => 2,
      "totals" => { "sub_total" => 41.18, "grand_total" => 44.32 },
      "items" => [{ "item_id" => "10450114", "name" => "Great Value Whole Milk, 1 Gallon" }]
    }.freeze

    OLDER = ORDER.merge(
      "order_id" => "100000000000001",
      "date" => "2025-02-03T10:00:00.000Z",
      "type" => "in-store"
    ).freeze

    # Deliberately adversarial to the ordering test: its id sorts *highest* of
    # the three while its date sits in the middle. Without it, the fixture ids
    # happened to correlate with their dates, and a comparator that ignored the
    # date entirely and sorted by id passed the whole suite.
    MIDDLE = ORDER.merge(
      "order_id" => "900000000000005",
      "display_id" => "9000000-00005",
      "date" => "2025-11-14T09:30:00.000Z"
    ).freeze

    # Silences the belt-and-braces chmod so a test can observe the mode the
    # file was *created* with. minitest/mock is not available here, so this
    # swaps the real method and puts it back.
    def without_chmod
      File.singleton_class.alias_method(:real_chmod, :chmod)
      File.singleton_class.define_method(:chmod) { |*_args| 0 }
      yield
    ensure
      File.singleton_class.alias_method(:chmod, :real_chmod)
      File.singleton_class.remove_method(:real_chmod)
    end

    def archive(**kwargs)
      Archive.new(paths: paths, now: -> { Time.utc(2026, 8, 26, 3, 0, 0) }, **kwargs)
    end

    # --- Never synced -------------------------------------------------------------
    #
    # "We have never looked" is a third state. Rounding it off to complete is
    # the exact lie this layer exists to prevent, and it would be told by the
    # code written to prevent it.

    def test_a_fresh_archive_has_not_been_synced
      refute_predicate archive, :synced?
      refute_predicate archive, :complete?
    end

    def test_a_fresh_archive_is_not_resumable_because_there_is_nothing_to_resume
      refute_predicate archive, :resumable?
    end

    def test_a_finished_walk_is_complete
      store = archive
      store.note_sync(reason: "complete")

      assert_predicate store, :complete?
      refute_predicate store, :resumable?
    end

    def test_an_interrupted_walk_is_synced_but_not_complete
      store = archive
      store.note_sync(reason: "rate_limited", cursor: "CURSOR")

      assert_predicate store, :synced?
      refute_predicate store, :complete?
      assert_predicate store, :resumable?
    end

    # --- Writing ---------------------------------------------------------------

    def test_an_order_is_written_under_its_year
      archive.write(ORDER)

      assert_path_exists File.join(paths.orders_dir, "2026", "200012345678901.json")
    end

    def test_the_record_round_trips
      archive.write(ORDER)

      record = archive.record("200012345678901")

      assert_equal "delivery", record["type"]
      assert_equal "10450114", record["items"][0]["item_id"]
    end

    def test_the_record_says_when_it_was_synced_and_in_what_shape
      # Parser drift is diagnosed from these two fields. Without them, an old
      # record read by new code looks like a bug in Walmart.
      archive.write(ORDER)

      record = archive.record("200012345678901")

      assert_equal "2026-08-26T03:00:00Z", record["_synced_at"]
      assert_equal Archive::FORMAT, record["_format"]
    end

    def test_an_order_without_an_id_is_refused
      assert_raises(ArgumentError) { archive.write(ORDER.except("order_id")) }
    end

    # --- Permissions -------------------------------------------------------------
    #
    # Two years of groceries names what a household eats, drinks, and takes for
    # its health. The interesting reader is another process running as the same
    # user, not a stranger on the network.

    def test_records_are_owner_only
      archive.write(ORDER)
      path = File.join(paths.orders_dir, "2026", "200012345678901.json")

      assert_equal "600", format("%o", File.stat(path).mode & 0o777)
    end

    def test_the_index_is_owner_only
      archive.write(ORDER)

      assert_equal "600", format("%o", File.stat(paths.index_file).mode & 0o777)
    end

    def test_archive_directories_are_owner_only
      archive.write(ORDER)

      assert_equal "700", format("%o", File.stat(File.join(paths.orders_dir, "2026")).mode & 0o777)
    end

    # The mode of the file that survives cannot see the bug this guards
    # against: write-then-chmod leaves the file world-readable for the instant
    # between the two calls, and that instant contains a whole order. Removing
    # the chmod leaves only the creation mode visible, which is the thing that
    # actually has to be right. A permissive umask makes the failure loud.
    def test_a_record_is_never_world_readable_even_for_an_instant
      original_umask = File.umask(0)
      without_chmod do
        archive.write(ORDER)
      end
      path = File.join(paths.orders_dir, "2026", "200012345678901.json")

      assert_equal "600", format("%o", File.stat(path).mode & 0o777)
    ensure
      File.umask(original_umask)
    end

    def test_the_index_is_never_world_readable_even_for_an_instant
      original_umask = File.umask(0)
      without_chmod { archive.write(ORDER) }

      assert_equal "600", format("%o", File.stat(paths.index_file).mode & 0o777)
    ensure
      File.umask(original_umask)
    end

    # A leftover .tmp means a previous run died mid-write. Writing through it
    # would be writing into a file we know nothing about.
    def test_a_stale_temporary_file_is_an_error_not_something_to_write_through
      store = archive
      store.write(ORDER)
      stale = File.join(paths.orders_dir, "2026", "200012345678901.json.tmp")
      File.write(stale, "leftover")

      assert_raises(ConfigError) { store.write(ORDER, force: true) }
    end

    def test_no_temporary_file_survives_a_write
      # A leftover .tmp is both clutter and a second copy of the same data.
      archive.write(ORDER)

      assert_empty Dir.glob(File.join(paths.orders_dir, "**", "*.tmp"))
    end

    # --- Dedupe --------------------------------------------------------------------

    def test_a_second_write_of_the_same_order_is_skipped
      store = archive
      store.write(ORDER)

      assert_equal :unchanged, store.write(ORDER)
    end

    def test_force_rewrites_a_known_order
      store = archive
      store.write(ORDER)

      assert_equal :written, store.write(ORDER.merge("item_count" => 3), force: true)
      assert_equal 3, store.record("200012345678901")["item_count"]
    end

    def test_dedupe_survives_a_restart
      archive.write(ORDER)

      assert archive.archived?("200012345678901"), "a fresh Archive must read the index from disk"
      assert_equal :unchanged, archive.write(ORDER)
    end

    # --- The index ---------------------------------------------------------------

    def test_the_index_summarizes_without_repeating_the_record
      archive.write(ORDER)

      entry = archive.entry("200012345678901")

      assert_equal "delivery", entry["type"]
      assert_in_delta 44.32, entry["total"]
      assert_nil entry["items"]
    end

    # Three orders, written in an order that matches neither the answer nor its
    # reverse, with ids that contradict the dates. Two elements cannot tell a
    # date sort from "reverse whatever was inserted last", and ids that happen
    # to sort like their dates cannot tell a date sort from an id sort.
    def test_entries_come_back_newest_first
      store = archive
      store.write(MIDDLE)
      store.write(OLDER)
      store.write(ORDER)

      ids = store.entries.map { |row| row["order_id"] }

      assert_equal %w[200012345678901 900000000000005 100000000000001], ids
    end

    # Newest-first is not cosmetic. `order search` takes the first sighting of
    # an item as the current name and price, so a wrong order here quietly
    # reports what something used to cost.
    def test_an_undated_record_sorts_last_rather_than_first
      store = archive
      store.write(ORDER)
      store.write(ORDER.merge("order_id" => "300000000000003", "date" => nil))

      assert_equal "200012345678901", store.entries.first["order_id"]
    end

    def test_in_store_receipts_share_the_archive_with_online_orders
      store = archive
      store.write(ORDER)
      store.write(OLDER)

      types = store.entries.map { |row| row["type"] }

      assert_equal %w[delivery in-store], types
    end

    # A corrupt index is recoverable; the records on disk are the truth. Losing
    # two years of history because one file would not parse is not acceptable.
    def test_a_corrupt_index_is_rebuilt_from_the_records
      archive.write(ORDER)
      File.write(paths.index_file, "{ this is not json")

      rebuilt = archive

      assert rebuilt.archived?("200012345678901")
      assert_equal "delivery", rebuilt.entry("200012345678901")["type"]
    end

    def test_a_record_missing_from_disk_reads_as_absent_rather_than_crashing
      store = archive
      store.write(ORDER)
      FileUtils.rm(File.join(paths.orders_dir, "2026", "200012345678901.json"))

      assert_nil store.record("200012345678901")
    end

    # --- Paths ------------------------------------------------------------------

    def test_an_order_id_cannot_escape_the_archive_directory
      # Ids come from Walmart, not from us.
      archive.write(ORDER.merge("order_id" => "../../etc/passwd"))

      refute_path_exists File.join(paths.data_home, "..", "..", "etc", "passwd")
      assert_equal 1, Dir.glob(File.join(paths.orders_dir, "**", "*.json")).length
    end

    def test_an_unreadable_date_files_under_unknown_not_under_this_year
      # A record whose date we could not read is not a record from today, and
      # guessing scatters re-syncs across folders.
      archive.write(ORDER.merge("date" => nil))

      assert_path_exists File.join(paths.orders_dir, "unknown", "200012345678901.json")
    end

    # --- Sync state ----------------------------------------------------------------

    def test_a_finished_walk_is_recorded_as_complete
      store = archive
      store.note_sync(reason: "complete", oldest_date: "2024-08-26")

      refute_predicate store, :resumable?
      assert_equal "complete", store.sync_state["reason"]
    end

    # A partial archive that knows it is partial is fine. One that silently
    # believes it is complete is how someone searches two years of history and
    # is told, wrongly, that they never bought a thing.
    def test_a_walk_stopped_by_a_block_is_resumable
      store = archive
      store.note_sync(cursor: "CURSOR", reason: "challenge_detected")

      assert_predicate store, :resumable?
      assert_equal "CURSOR", store.sync_state["cursor"]
    end

    def test_sync_state_survives_a_restart
      archive.note_sync(cursor: "CURSOR", reason: "rate_limited")

      assert_predicate archive, :resumable?
    end

    def test_a_complete_walk_keeps_no_cursor
      # The cursor is session-scoped. Keeping one we have no use for is keeping
      # an identifier for no reason.
      store = archive
      store.note_sync(reason: "complete")

      assert_nil store.sync_state["cursor"]
    end
  end
end
