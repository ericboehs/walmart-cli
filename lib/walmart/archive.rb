# frozen_string_literal: true

require "json"
require "fileutils"
require "time"

module Walmart
  # The local order archive: one JSON file per order, plus an index.
  #
  # This is a complete record of what a household eats, drinks, and takes for
  # its health, going back two years. It is written 0700/0600 for the same
  # reason the browser profile is: the interesting attacker here is not a
  # stranger on the network, it is any other process running as the same user
  # on a shared or backed-up machine.
  #
  # Two properties the rest of the code depends on:
  #
  # 1. Writes are atomic. A sync interrupted mid-write leaves the previous
  #    record intact rather than a truncated one, because a half-written order
  #    would be read back as a real order with items missing.
  # 2. Re-syncing is a dedupe, not a refetch. The index remembers which ids are
  #    archived so a second run over two years of history costs nothing.
  class Archive
    include ArchiveReader

    # Bumped when the record shape changes in a way that makes old files
    # unreadable. Stored per record so a future reader can tell.
    FORMAT = 1

    # What one scan of the archive found. Records the archive cannot return,
    # split by whether re-fetching them would help.
    Integrity = Struct.new(:damaged, :incompatible, keyword_init: true) do
      def healthy? = damaged.empty? && incompatible.empty?

      def unreadable = damaged + incompatible
    end

    def initialize(paths:, now: -> { Time.now.utc })
      @paths = paths
      @now = now
    end

    attr_reader :paths

    # --- Reading ---------------------------------------------------------------

    def index
      @index ||= read_index
    end

    def archived?(order_id) = index["orders"].key?(order_id.to_s)

    def entry(order_id) = index["orders"][order_id.to_s]

    # Every index entry, newest first. Sorting here rather than at each call
    # site means `order list` and the reorder picker cannot disagree.
    def entries
      index["orders"].values.sort_by { |value| value["date"].to_s }.reverse
    end

    # The record for an id, or nil when we have never archived it.
    #
    # Raises ArchiveError when the index claims the order and the file cannot be
    # returned. Those are different facts and the caller acts on them
    # differently: "we never bought that" ends the search, "the file we wrote is
    # damaged" is a repair. Collapsing damage into nil would report corruption
    # as not-found and send someone re-syncing to fix a file a dedupe-by-id sync
    # will never rewrite.
    def record(order_id)
      id = order_id.to_s
      found = entry(id)
      return nil unless found

      # An index entry is meant to be a Hash. It is a file on disk, so "meant
      # to be" is not a fact: an array or a number here reached `["path"]` and
      # raised TypeError out of a reader that promises not to.
      raise damaged(id, "its index entry is not a record") unless found.is_a?(Hash)

      read_record(confined_path(found["path"], id), id)
    end

    # The record, or nil for any reason at all. Never raises.
    #
    # For bulk readers: one damaged file must not turn `order search` into a
    # crash across an otherwise intact archive.
    #
    # Both archive errors, not just the repairable one. This promise was made
    # while rescuing ArchiveError alone, and the layers below could still raise
    # ArgumentError, TypeError and Errno::* on sick input -- so the crash
    # happened inside damaged_ids, which `order sync` calls before it opens the
    # worker. A damaged archive crashed the repair path built for it. Those are
    # translated at the boundary now rather than swallowed here; this rescue
    # stays narrow so a genuine programming error is still visible.
    def readable_record(order_id)
      record(order_id)
    rescue ArchiveError, ArchiveIncompatibleError
      nil
    end

    def readable?(order_id) = !readable_record(order_id).nil?

    # Ids whose records we can actually return.
    #
    # This is what may travel as known_ids. Index membership was used for that
    # and it is the wrong predicate: an entry surviving a missing or corrupt
    # file told the worker to skip the detail page forever, so an ordinary sync
    # could never repair the archive. "Known" has to mean "we can return it".
    def readable_ids = index["orders"].keys.select { |id| readable?(id) }

    # Ids the index claims and cannot produce *and could repair by re-fetching*.
    # Incompatible records are excluded: re-fetching one would not help, and
    # writing it is the thing we must not do.
    def damaged_ids = integrity.damaged

    # Ids written by a newer version of this CLI.
    def incompatible_ids = integrity.incompatible

    # One pass over the index, classifying every record exactly once.
    #
    # damaged_ids and incompatible_ids each used to walk the whole archive and
    # reparse every file, so a render asking for both scanned twice -- and could
    # answer from two different views of the disk, emitting one JSON document
    # claiming complete:true and healthy:false. Take a snapshot and derive
    # everything from it.
    def integrity
      damaged = []
      incompatible = []
      index["orders"].each_key do |id|
        case failure_for(id)
        when ArchiveIncompatibleError then incompatible << id
        when ArchiveError then damaged << id
        end
      end
      Integrity.new(damaged: damaged, incompatible: incompatible)
    end

    # --- Writing ---------------------------------------------------------------

    # Writes one order and updates the index. Returns :written or :unchanged.
    #
    # +force+ rewrites a record we already have; without it an archived id is
    # skipped, which is what makes an incremental sync cheap.
    def write(order, force: false)
      order_id = order["order_id"].to_s
      raise ArgumentError, "an order needs an id to be archived" if order_id.empty?

      # Refused before the shortcut, and before force is even considered. An
      # older CLI declared this record unreadable and then overwrote it,
      # dropping fields it did not understand -- refusing to read something and
      # then destroying it is the worst available pairing, and --full did it
      # too. force means "rewrite a record we understand", never "discard one we
      # do not".
      assert_writable!(order_id)

      # readable?, not archived?. The shortcut exists to make an incremental
      # sync cheap, and skipping a rewrite because a *broken* entry exists is
      # the opposite: it discards the replacement that would have repaired it.
      return :unchanged if readable?(order_id) && !force

      refuse_unredacted!(order_id, order)
      record = order.merge("_synced_at" => timestamp, "_format" => FORMAT)
      relative = relative_path(order_id, order["date"])
      write_json(File.join(paths.orders_dir, relative), record)
      index["orders"][order_id] = index_entry(order, relative)
      save_index
      :written
    end

    # Records how far a walk got and why it stopped. A partial archive that
    # knows it is partial is fine; one that silently believes it is complete is
    # not, so the reason is stored even when it is "finished".
    # No cursor is stored. One used to be, described as kept "so the next run
    # can pick up where it left off" -- and nothing ever read it. The walk in
    # orders.py clicks Next rather than replaying a cursor, deliberately,
    # because replaying one earned a 429. So the field was an opaque
    # session-scoped identifier written to disk for no operational purpose,
    # which the minimization rule forbids on its own, and it made the archive
    # look resumable when it is not.
    def note_sync(reason: "complete", oldest_date: nil, since: nil, before: nil)
      index["sync"] = {
        "at" => timestamp,
        "reason" => reason,
        # The window that was asked for. Without it a cutoff cannot be told
        # apart from a truncation: both stopped before the end of history, but
        # one stopped exactly where it was told to.
        "since" => since,
        # The upper end, when the window had one. A bounded window and an
        # open-topped one make different coverage claims.
        "before" => before,
        "oldest_date" => oldest_date
      }.compact
      save_index
      index["sync"]
    end

    def sync_state = index["sync"] || {}

    # The stop note as a value object. Settlement is a property of the whole
    # note, not of the reason alone -- see SyncNote.
    def note = SyncNote.from(sync_state)

    # Whether a walk has ever run against this archive.
    #
    # Distinct from complete and from incomplete, because "we have never looked"
    # is a third state and the most misleading one to round off. An archive
    # that has never synced is not complete, and it is not incomplete either --
    # there is nothing to fill in.
    def synced? = !sync_state.empty?

    # True when the last walk ended for a reason that leaves history unseen.
    #
    # Named for what it knows. It was called resumable?, which claimed a
    # capability the tool does not have: a second sync restarts at page one. It
    # answers "is there history we have not read", not "can the next run
    # continue from where this one stopped". A never-synced archive is false
    # here and must not be read as complete on that basis.
    #
    # A cutoff is not incomplete. It means the walk reached the edge of the
    # window it was given and stopped on purpose. Counting it as "stopped early"
    # made `order sync --year 2026` exit 0 in silence and then made `order list`
    # call the same run a truncation and advise re-running it -- advice that
    # produces the identical cutoff every time.
    def incomplete? = synced? && !note.settled?

    # Reached the end of what was asked for, whether or not that was all of
    # history. `complete?` stays narrower on purpose: it is the claim a machine
    # consumer acts on, and it must mean "there is nothing older".
    def window_complete? = synced? && note.settled?

    # The window the last sync was given, if it was given one.
    def requested_since = sync_state["since"]

    def requested_before = sync_state["before"]

    # Everything Walmart still shows is on disk.
    #
    # Requires no window at all, not merely a finished walk. "complete" from the
    # worker means "no next cursor", which proves we reached the oldest page and
    # says nothing about the rows we skipped getting there. `--year 2024` skips
    # newer rows all the way down and still ends with no cursor. The lower bound
    # has the same hole: a final page holding one in-window row and one older
    # row is not a frontier, so the walk ends COMPLETE having skipped the old
    # one. Only an unwindowed run fetches every row it passes.
    def complete? = synced? && note.all_history?

    # Was this sync asked for part of history rather than all of it?
    def windowed? = note.windowed?

    private

    def timestamp = @now.call.iso8601

    def index_entry(order, relative)
      {
        "order_id" => order["order_id"].to_s,
        "display_id" => order["display_id"],
        "date" => order["date"],
        "type" => order["type"],
        "item_count" => order["item_count"],
        "total" => order.dig("totals", "grand_total"),
        "path" => relative,
        "synced_at" => timestamp
      }.compact
    end

    # orders/<year>/<id>.json, with unknown dates filed under "unknown" rather
    # than under this year. A record whose date we could not read is not a
    # record from today, and guessing would scatter re-syncs across folders.
    def relative_path(order_id, date)
      File.join(year_of(date), "#{safe_id(order_id)}.json")
    end

    def year_of(date)
      text = date.to_s
      match = text.match(/\A(\d{4})-\d{2}/)
      match ? match[1] : "unknown"
    end

    # Order ids come from Walmart, not from us. A path separator or a ".." in
    # one must never become a directory traversal.
    def safe_id(order_id)
      cleaned = order_id.to_s.gsub(/[^A-Za-z0-9._-]/, "-").sub(/\A\.+/, "")
      cleaned.empty? ? "order" : cleaned
    end

    def read_index
      default = { "version" => FORMAT, "orders" => {}, "sync" => {} }
      return default unless File.exist?(paths.index_file)

      parsed = JSON.parse(File.read(paths.index_file))
      return default unless parsed.is_a?(Hash) && parsed["orders"].is_a?(Hash)

      default.merge(parsed)
    rescue JSON::ParserError
      # A corrupt index is recoverable: the records on disk are the truth, and
      # a rebuilt index costs one directory walk. Losing them because a single
      # file would not parse is not.
      default.merge("orders" => rebuild_orders)
    end

    def rebuild_orders
      Dir.glob(File.join(paths.orders_dir, "*", "*.json")).each_with_object({}) do |path, found|
        parsed = JSON.parse(File.read(path))
        order_id = parsed["order_id"].to_s
        next if order_id.empty?

        relative = path.delete_prefix("#{paths.orders_dir}/")
        found[order_id] = index_entry(parsed, relative)
      rescue JSON::ParserError
        next
      end
    end

    def save_index
      write_json(paths.index_file, index)
    end

    # Writes a file that is never, at any instant, more permissive than 0600.
    #
    # The obvious version -- write, then chmod -- leaves the file readable at
    # whatever the umask allows for the moment between the two calls, and that
    # moment contains the entire contents of an order. Creating it with the
    # mode closes the window. The trailing chmod stays because File.open's mode
    # is itself subject to umask, so this is certainty rather than nearly.
    #
    # EXCL also turns a leftover .tmp from a crashed run into an error instead
    # of something we silently write through.
    # The last thing between a parser bug and a card number on disk, in a file
    # we told the user was safe to keep for two years.
    #
    # The worker already removes these, so reaching here means the removal
    # stopped working -- which is exactly why the check lives on this side of
    # the pipe and reads its own list. It refuses the write rather than
    # sanitizing: a record we do not understand should not be quietly repaired
    # and stored, and the archive missing one order is a smaller problem than
    # the archive holding someone's address.
    def refuse_unredacted!(order_id, order)
      leaked = Redact.forbidden_keys(order)
      return if leaked.empty?

      raise Error.new(
        "refusing to archive order #{order_id}: it still carries #{leaked.sort.join(", ")}",
        code: "unredacted_field"
      )
    end

    def write_json(path, data)
      paths.ensure_private_dir!(File.dirname(path))
      tmp = "#{path}.tmp"
      body = "#{JSON.pretty_generate(data)}\n"
      File.open(tmp, File::WRONLY | File::CREAT | File::EXCL, 0o600) { |file| file.write(body) }
      File.chmod(0o600, tmp)
      # rename is atomic within a filesystem: a reader sees the old file or the
      # new one, never a partial write.
      File.rename(tmp, path)
      path
    rescue Errno::EEXIST
      raise ConfigError, "a previous write left #{tmp} behind; remove it and try again"
    end
  end
end
