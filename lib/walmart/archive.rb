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
    # Bumped when the record shape changes in a way that makes old files
    # unreadable. Stored per record so a future reader can tell.
    FORMAT = 1

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
    def damaged_ids = index["orders"].keys.select { |id| failure_for(id).is_a?(ArchiveError) }

    # Ids written by a newer version of this CLI.
    def incompatible_ids
      index["orders"].keys.select { |id| failure_for(id).is_a?(ArchiveIncompatibleError) }
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

    # Stop reasons that mean "we read everything we set out to read".
    WINDOW_SETTLED = %w[complete cutoff].freeze

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
    def incomplete? = synced? && !WINDOW_SETTLED.include?(sync_state["reason"])

    # Reached the end of what was asked for, whether or not that was all of
    # history. `complete?` stays narrower on purpose: it is the claim a machine
    # consumer acts on, and it must mean "there is nothing older".
    def window_complete? = synced? && WINDOW_SETTLED.include?(sync_state["reason"])

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
    def complete? = synced? && sync_state["reason"] == "complete" && !windowed?

    # Was this sync asked for part of history rather than all of it?
    def windowed? = !(requested_since.nil? && requested_before.nil?)

    private

    def timestamp = @now.call.iso8601

    # Resolves an index path inside the archive, or refuses.
    #
    # The writer only ever creates confined paths, so today this cannot trigger
    # -- which is exactly why it is worth asserting. The reader trusted the
    # index, and the index is a file on disk; an entry with ../.. in it reads
    # any JSON on the filesystem, and a symlink in the orders tree does the same
    # with a perfectly ordinary-looking path. Both reproduce. Making the
    # invariant executable keeps it true of tomorrow's writer too.
    def confined_path(relative, id)
      raise damaged(id, "its index entry has no file path") unless relative.is_a?(String) && !relative.empty?

      # expand_path, not realpath. An index can list orders while orders/ does
      # not exist -- an archive whose only sync hit a challenge is exactly that
      # shape -- and realpath raises ENOENT on a missing directory. Resolving
      # the root eagerly made the containment check fail with a raw backtrace
      # in precisely the case this method exists to diagnose.
      root = File.expand_path(paths.orders_dir)
      # A null byte in the path raises ArgumentError out of every File method.
      path = expanded(root, relative, id)
      raise damaged(id, "its index entry points outside the archive") unless inside?(path, root)
      raise damaged(id, "its file is missing") unless File.exist?(path)

      resolve_link(path, id)
    end

    # Symlinks are resolved only once the target is known to exist, because
    # realpath needs a real file at both ends. Both sides are canonicalised
    # here so a symlinked root (/var -> /private/var on macOS) does not read as
    # an escape.
    def resolve_link(path, id)
      real = File.realpath(path)
      root = File.realpath(paths.orders_dir)
      raise damaged(id, "its file links outside the archive") unless inside?(real, root)
      raise damaged(id, "its path is not a regular file") unless File.file?(real)

      real
    rescue SystemCallError => e
      # The file can vanish, or become a symlink loop, between the existence
      # check and here.
      raise damaged(id, "its file could not be resolved (#{e.class.name.split("::").last})")
    end

    def inside?(path, root) = path.start_with?("#{root}/")

    def expanded(root, relative, id)
      File.expand_path(File.join(root, relative))
    rescue ArgumentError
      raise damaged(id, "its index entry is not a usable path")
    end

    def read_record(path, id)
      data = JSON.parse(read_file(path, id))
      raise damaged(id, "its file does not hold an order") unless data.is_a?(Hash)

      # Parseable is not the same as ours. Without this, one order's file can
      # satisfy another order's skip, and the archive reports an order it has
      # never actually stored.
      # Before the id check, and before every other content test. Once the file
      # says it was written by a newer version, this CLI has admitted it does
      # not understand the contents -- so it is in no position to decide that
      # some *other* thing about those contents makes the file disposable. A
      # newer record that also had a mismatched id was classified as repairable
      # damage and destroyed.
      assert_format!(data, id)

      found = data["order_id"].to_s
      raise damaged(id, "its file holds order #{found}") unless found == id

      data
    rescue JSON::ParserError
      raise damaged(id, "its file is not valid JSON")
    end

    # A newer format is refused and preserved; anything else unexpected is
    # damage, which a re-fetch repairs safely because the result is our own
    # format. Absent or non-integer counts as damage rather than incompatible:
    # a missing marker is not evidence of a newer writer, and every record this
    # CLI has ever written carries one.
    def assert_format!(data, id)
      format = data["_format"]
      unless format.is_a?(Integer) && format.positive?
        raise damaged(id, "its file does not say what format it is in")
      end
      return unless format > FORMAT

      raise ArchiveIncompatibleError.new(
        "order #{id} was written by a newer version of walmart-cli (format #{format}); " \
        "this one understands format #{FORMAT}",
        details: { "order_id" => id, "format" => format }
      )
    end

    # Filesystem calls that can fail for reasons that are not bugs. A path can
    # be unreadable, or vanish between the existence check and the read.
    def read_file(path, id)
      File.read(path)
    rescue SystemCallError => e
      raise damaged(id, "its file could not be read (#{e.class.name.split("::").last})")
    end

    def damaged(id, why)
      ArchiveError.new("the archive lists order #{id} but #{why}",
                       details: { "order_id" => id })
    end

    # The error a read of this id would raise, or nil if it reads cleanly.
    # One place decides, so damaged_ids and incompatible_ids cannot disagree
    # about which bucket a record is in.
    def failure_for(order_id)
      record(order_id)
      nil
    rescue ArchiveError, ArchiveIncompatibleError => e
      e
    end

    def assert_writable!(order_id)
      failure = failure_for(order_id)
      raise failure if failure.is_a?(ArchiveIncompatibleError)
    end

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
