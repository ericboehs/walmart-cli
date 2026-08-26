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

    # The worker removes these before they get here, so this guard should never
    # fire in practice -- which is precisely why it needs its own test. A guard
    # that has never fired is a guard nobody has watched work.
    def test_a_record_that_still_carries_payment_details_is_refused
      archive = archive()
      poisoned = ORDER.merge("paymentMethods" => [{ "cardType" => "VISA", "lastFour" => "4242" }])

      error = assert_raises(Error) { archive.write(poisoned) }

      assert_equal "unredacted_field", error.code
      refute_includes error.message, "4242", "the refusal must not quote what it caught"
      assert_empty Dir.glob(File.join(paths.orders_dir, "**", "*.json")),
                   "nothing may reach disk once a forbidden field is seen"
    end

    # The realistic leak is not a top-level field, it is one buried in a group
    # inside an item, which a shallow check would archive happily.
    def test_a_forbidden_field_nested_deep_inside_an_order_is_refused
      archive = archive()
      poisoned = ORDER.merge(
        "items" => [{ "item_id" => "1", "seller" => { "contact" => { "phoneNumber" => "555-0100" } } }]
      )

      error = assert_raises(Error) { archive.write(poisoned) }

      assert_equal "unredacted_field", error.code
    end

    # The other half: the guard has to let a real record through, or it would
    # have been discovered by breaking every sync rather than by this test.
    def test_an_ordinary_redacted_order_is_archived_normally
      archive = archive()

      assert_equal :written, archive.write(ORDER)
    end

    def test_a_fresh_archive_has_not_been_synced
      refute_predicate archive, :synced?
      refute_predicate archive, :complete?
    end

    def test_a_fresh_archive_is_not_resumable_because_there_is_nothing_to_resume
      refute_predicate archive, :incomplete?
    end

    def test_a_finished_walk_is_complete
      store = archive
      store.note_sync(reason: "complete")

      assert_predicate store, :complete?
      refute_predicate store, :incomplete?
    end

    def test_an_interrupted_walk_is_synced_but_not_complete
      store = archive
      store.note_sync(reason: "rate_limited")

      assert_predicate store, :synced?
      refute_predicate store, :complete?
      assert_predicate store, :incomplete?
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

    # This test used to assert the opposite -- that a missing file "reads as
    # absent" -- and that collapse is the defect. It made damage and
    # never-bought-it the same answer, which is how a broken entry could keep
    # telling the worker to skip the order forever. The valid concern underneath
    # it, that a bulk reader must not crash, is served by readable_record below.
    def test_a_missing_file_is_damage_and_says_which_order
      store = archive
      store.write(ORDER)
      FileUtils.rm(File.join(paths.orders_dir, "2026", "200012345678901.json"))

      error = assert_raises(ArchiveError) { store.record("200012345678901") }
      assert_match(/200012345678901/, error.message)
      assert_match(/missing/, error.message)
    end

    def test_an_order_we_never_archived_is_nil_not_damage
      assert_nil archive.record("999999999999999")
    end

    def test_a_corrupt_file_is_damage_not_absence
      store = archive
      store.write(ORDER)
      File.write(File.join(paths.orders_dir, "2026", "200012345678901.json"), "{")

      assert_raises(ArchiveError) { store.record("200012345678901") }
    end

    # Parseable is not the same as ours. Without the id check one order's file
    # satisfies another order's skip.
    def test_a_file_holding_a_different_order_is_damage
      store = archive
      store.write(ORDER)
      path = File.join(paths.orders_dir, "2026", "200012345678901.json")
      # A current-format marker, so this exercises the id check rather than
      # tripping the format check that now runs ahead of it.
      File.write(path, JSON.dump(ORDER.merge("order_id" => "300000000000000", "_format" => 1)))

      error = assert_raises(ArchiveError) { store.record("200012345678901") }
      assert_match(/holds order 300000000000000/, error.message)
    end

    def test_a_bulk_reader_gets_nil_rather_than_an_exception
      store = archive
      store.write(ORDER)
      File.write(File.join(paths.orders_dir, "2026", "200012345678901.json"), "{")

      assert_nil store.readable_record("200012345678901")
    end

    # --- Known means returnable --------------------------------------------------

    # The whole point of the change. An index entry surviving a damaged file
    # used to travel to the worker as known_ids, so the detail page was skipped
    # on every sync and the archive could never repair itself.
    def test_a_damaged_record_is_not_a_known_id
      store = archive
      store.write(ORDER)
      FileUtils.rm(File.join(paths.orders_dir, "2026", "200012345678901.json"))

      assert_empty store.readable_ids
      assert_equal ["200012345678901"], store.damaged_ids
      assert store.archived?("200012345678901"), "the index still lists it"
    end

    def test_an_intact_record_is_a_known_id
      store = archive
      store.write(ORDER)

      assert_equal ["200012345678901"], store.readable_ids
      assert_empty store.damaged_ids
    end

    # Step five of the chain: even when the worker did return the order again,
    # the unchanged shortcut discarded it, because it asked the index rather
    # than the disk.
    def test_a_returned_order_repairs_a_damaged_record_without_force
      store = archive
      store.write(ORDER)
      path = File.join(paths.orders_dir, "2026", "200012345678901.json")
      FileUtils.rm(path)

      assert_equal :written, store.write(ORDER)
      assert_path_exists path
      assert_equal "200012345678901", store.record("200012345678901")["order_id"]
    end

    def test_an_intact_record_is_still_skipped_without_force
      store = archive
      store.write(ORDER)

      assert_equal :unchanged, store.write(ORDER)
    end

    # --- Reader path confinement ---------------------------------------------------

    # Both of these reproduce against the old reader: it joined orders_dir with
    # the index path and trusted it. The writer never creates such an entry,
    # which is the reason to assert it rather than the reason not to.
    def test_an_index_path_cannot_escape_the_archive
      store = archive
      store.write(ORDER)
      outside = File.join(tmp, "elsewhere.json")
      File.write(outside, JSON.dump({ "order_id" => "200012345678901" }))
      store.index["orders"]["200012345678901"]["path"] = "../../elsewhere.json"

      error = assert_raises(ArchiveError) { store.record("200012345678901") }
      assert_match(/outside the archive/, error.message)
    end

    def test_a_symlink_out_of_the_archive_is_refused
      store = archive
      store.write(ORDER)
      outside = File.join(tmp, "elsewhere.json")
      File.write(outside, JSON.dump({ "order_id" => "200012345678901" }))
      path = File.join(paths.orders_dir, "2026", "200012345678901.json")
      FileUtils.rm(path)
      File.symlink(outside, path)

      error = assert_raises(ArchiveError) { store.record("200012345678901") }
      assert_match(/links outside the archive/, error.message)
    end

    # An index can list orders while orders/ has never been created: an archive
    # whose only sync hit a challenge is exactly that shape. Canonicalising the
    # root eagerly raised a raw ENOENT here -- in the very case this API exists
    # to diagnose -- and readable_ids inherited it, so `order sync` would have
    # died building its own known_ids.
    def test_an_index_without_an_orders_directory_reports_missing_not_enoent
      FileUtils.mkdir_p(File.dirname(paths.index_file))
      File.write(paths.index_file, JSON.dump(
                                     "version" => 1,
                                     "orders" => {
                                       "200012345678901" => { "path" => "2026/200012345678901.json",
                                                              "date" => "2026-08-20" }
                                     },
                                     "sync" => { "reason" => "challenge_detected" }
                                   ))
      store = Archive.new(paths: paths)

      refute_path_exists paths.orders_dir
      error = assert_raises(ArchiveError) { store.record("200012345678901") }
      assert_match(/its file is missing/, error.message)
      assert_empty store.readable_ids
      assert_equal ["200012345678901"], store.damaged_ids
    end

    def test_a_sync_can_repair_an_archive_that_has_no_orders_directory
      FileUtils.mkdir_p(File.dirname(paths.index_file))
      File.write(paths.index_file, JSON.dump(
                                     "version" => 1,
                                     "orders" => {
                                       "200012345678901" => { "path" => "2026/200012345678901.json",
                                                              "date" => "2026-08-20" }
                                     },
                                     "sync" => {}
                                   ))
      store = Archive.new(paths: paths)

      assert_equal :written, store.write(ORDER)
      assert_equal "200012345678901", store.record("200012345678901")["order_id"]
    end

    # --- the reader never raises on sick input -------------------------------------

    # Table-driven because the promise is "nil for any reason at all", and the
    # first version of it rescued one error class while the layers below could
    # raise ArgumentError, TypeError and Errno::*. Each case below did escape.
    # damaged_ids is asserted too: `order sync` calls it before opening the
    # worker, so a damaged archive crashed the repair path built for it.
    SICK_ARCHIVES = {
      "a null byte in the path" => lambda { |store, _path|
        store.index["orders"]["200012345678901"]["path"] = "2026/evil\0.json"
      },
      "an index entry that is an array" => lambda { |store, _path|
        store.index["orders"]["200012345678901"] = []
      },
      "an index entry that is a number" => lambda { |store, _path|
        store.index["orders"]["200012345678901"] = 7
      },
      "an index entry that is a string" => lambda { |store, _path|
        store.index["orders"]["200012345678901"] = "2026/200012345678901.json"
      },
      "a path that is not a string" => lambda { |store, _path|
        store.index["orders"]["200012345678901"]["path"] = 7
      },
      "a file we may not read" => ->(_store, path) { File.chmod(0o000, path) },
      "a file that is gone" => ->(_store, path) { FileUtils.rm(path) },
      "a file that is not JSON" => ->(_store, path) { File.write(path, "{") },
      "a file holding another order" => lambda { |_store, path|
        File.write(path, JSON.dump("order_id" => "999", "_format" => 1))
      },
      "a record from a newer walmart-cli" => lambda { |_store, path|
        File.write(path, JSON.dump(ORDER.merge("order_id" => "200012345678901", "_format" => 99)))
      }
    }.freeze

    SICK_ARCHIVES.each do |name, break_it|
      define_method(:"test_the_reader_returns_nil_for_#{name.tr(" ", "_")}") do
        store = archive
        store.write(ORDER)
        path = File.join(paths.orders_dir, "2026", "200012345678901.json")
        break_it.call(store, path)

        assert_nil store.readable_record("200012345678901")
        refute store.readable?("200012345678901")
        # The bucketing must not raise either.
        assert_kind_of Array, store.damaged_ids
        assert_kind_of Array, store.incompatible_ids
      ensure
        File.chmod(0o600, path) if path && File.exist?(path)
      end
    end

    # --- a newer format is preserved, not repaired -----------------------------------

    # The worst available pairing: read_record correctly refused a format it
    # does not understand, and then write treated "unreadable" as "repairable"
    # and overwrote it, dropping every field the newer version had added.
    def test_a_record_from_a_newer_cli_survives_an_ordinary_write
      store, path = newer_format_record

      assert_raises(ArchiveIncompatibleError) { store.write(ORDER) }
      assert_equal 2, JSON.parse(File.read(path))["_format"]
      assert_equal "keep me", JSON.parse(File.read(path))["newer_only"]
    end

    # force means "rewrite a record we understand", never "discard one we do
    # not". --full took the same destructive path.
    def test_force_does_not_overwrite_a_newer_record_either
      store, path = newer_format_record

      assert_raises(ArchiveIncompatibleError) { store.write(ORDER, force: true) }
      assert_equal "keep me", JSON.parse(File.read(path))["newer_only"]
    end

    def test_a_newer_record_is_not_called_damaged
      store, = newer_format_record

      assert_empty store.damaged_ids, "re-fetching would not help and writing is forbidden"
      assert_equal ["200012345678901"], store.incompatible_ids
    end

    # The same file, wrong in two ways at once. The id mismatch was noticed
    # first and made the record "repairable", so the newer format never got a
    # say and the bytes were overwritten. Once a file claims a format we do not
    # understand, we are in no position to judge anything else about its
    # contents.
    def test_a_newer_record_with_a_mismatched_id_is_still_protected
      store = archive
      store.write(ORDER)
      path = File.join(paths.orders_dir, "2026", "200012345678901.json")
      File.write(path, JSON.dump("order_id" => "300000000000000", "_format" => 2,
                                 "newer_only" => "keep me"))
      kept = File.read(path)
      reopened = Archive.new(paths: paths)

      assert_raises(ArchiveIncompatibleError) { reopened.record("200012345678901") }
      assert_raises(ArchiveIncompatibleError) { reopened.write(ORDER) }
      assert_raises(ArchiveIncompatibleError) { reopened.write(ORDER, force: true) }
      assert_equal kept, File.read(path)
      assert_equal ["200012345678901"], reopened.incompatible_ids
      assert_empty reopened.damaged_ids
    end

    def test_show_says_upgrade_rather_than_calling_it_damage
      store, = newer_format_record

      error = assert_raises(ArchiveIncompatibleError) { store.record("200012345678901") }
      assert_match(/newer version of walmart-cli/, error.message)
      assert_match(/format 2/, error.message)
    end

    # A missing marker is not evidence of a newer writer, and every record this
    # CLI has written carries one, so it is damage and a re-fetch repairs it.
    def test_a_record_with_no_format_marker_is_repairable_damage
      store = archive
      store.write(ORDER)
      path = File.join(paths.orders_dir, "2026", "200012345678901.json")
      File.write(path, JSON.dump(ORDER.merge("order_id" => "200012345678901")))

      assert_equal ["200012345678901"], store.damaged_ids
      assert_empty store.incompatible_ids
      assert_equal :written, store.write(ORDER)
    end

    # An archive holding one record written by a newer walmart-cli.
    def newer_format_record
      store = archive
      store.write(ORDER)
      path = File.join(paths.orders_dir, "2026", "200012345678901.json")
      File.write(path, JSON.dump(JSON.parse(File.read(path)).merge("_format" => 2,
                                                                   "newer_only" => "keep me")))
      [Archive.new(paths: paths), path]
    end

    def test_a_directory_where_a_record_should_be_is_refused
      store = archive
      store.write(ORDER)
      path = File.join(paths.orders_dir, "2026", "200012345678901.json")
      FileUtils.rm(path)
      FileUtils.mkdir_p(path)

      assert_raises(ArchiveError) { store.record("200012345678901") }
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

      refute_predicate store, :incomplete?
      assert_equal "complete", store.sync_state["reason"]
    end

    # A partial archive that knows it is partial is fine. One that silently
    # believes it is complete is how someone searches two years of history and
    # is told, wrongly, that they never bought a thing.
    def test_a_walk_stopped_by_a_block_is_marked_incomplete
      store = archive
      store.note_sync(reason: "challenge_detected")

      assert_predicate store, :incomplete?
    end

    # `order sync --year 2026` exits 0 with no warning, because cutoff is a
    # settled reason. The archive then called that same run "stopped early",
    # said it was incomplete, and advised running `walmart order sync` to fill
    # it in -- a command that reaches the identical cutoff every time. Two
    # components, each self-consistent, disagreeing about whether the same run
    # succeeded. It is the page-ceiling false remedy again, one reason over.
    def test_a_cutoff_is_not_a_truncated_sync
      store = archive
      store.note_sync(reason: "cutoff", since: "2026-01-01", oldest_date: "2026-01-02")

      refute_predicate store, :incomplete?
      assert_predicate store, :window_complete?
    end

    # But it is not complete history either, and complete? is the claim a
    # machine consumer acts on.
    def test_a_cutoff_is_not_complete_history
      store = archive
      store.note_sync(reason: "cutoff", since: "2026-01-01")

      refute_predicate store, :complete?
    end

    def test_a_full_walk_is_complete_and_window_complete_both
      store = archive
      store.note_sync(reason: "complete")

      assert_predicate store, :complete?
      assert_predicate store, :window_complete?
      refute_predicate store, :incomplete?
    end

    def test_a_truncated_walk_is_neither
      %w[page_limit challenge_detected rate_limited no_next_page].each do |reason|
        store = Archive.new(paths: paths)
        store.note_sync(reason: reason)

        assert_predicate store, :incomplete?, "#{reason} should read as incomplete"
        refute_predicate store, :window_complete?, "#{reason} should not read as settled"
      end
    end

    # Without the requested window, a cutoff cannot be told apart from a
    # truncation: both stopped before the end of history.
    def test_the_requested_window_is_recorded_with_the_stop
      store = archive
      store.note_sync(reason: "cutoff", since: "2025-01-01")

      assert_equal "2025-01-01", store.requested_since
    end

    def test_sync_state_survives_a_restart
      archive.note_sync(reason: "rate_limited")

      assert_predicate archive, :incomplete?
    end

    # The archive used to persist the pagination cursor, with a comment saying
    # it was kept so the next run could pick up where it left off. Nothing ever
    # read it. It was an opaque session-scoped identifier written to disk for no
    # operational purpose, and its presence made the archive look resumable.
    #
    # The old test asserted the cursor survived a restart -- that the writer
    # wrote and the reader could read the same field. It never asked whether a
    # consumer existed. That is the night's recurring shape: proving the
    # mechanism runs, not that anything depends on it.
    def test_no_stop_reason_writes_a_cursor_to_disk
      %w[complete cutoff page_limit challenge_detected rate_limited].each do |reason|
        store = Archive.new(paths: paths)
        store.note_sync(reason: reason)

        refute_includes store.sync_state.keys, "cursor", "#{reason} persisted a cursor"
        refute_includes File.read(paths.index_file), "cursor"
      end
    end

    # The guard above only holds while nothing reintroduces a reader. If a
    # cursor is ever persisted again it must be because something consumes it,
    # and this fails until that is true in the code rather than in a comment.
    def test_nothing_in_the_tree_reads_a_stored_cursor
      root = File.expand_path("../..", __dir__)
      readers = Dir[File.join(root, "lib/**/*.rb")].select do |file|
        File.read(file).match?(/sync_state\[.cursor.\]|sync\[.cursor.\]/)
      end

      assert_empty readers.map { |file| file.delete_prefix("#{root}/") },
                   "something reads a stored cursor; either the archive should be storing one " \
                   "again, or this reader is acting on a field that is always nil"
    end
  end
end
