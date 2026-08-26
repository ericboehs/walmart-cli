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

    def record(order_id)
      entry = entry(order_id)
      return nil unless entry

      path = File.join(paths.orders_dir, entry["path"])
      return nil unless File.exist?(path)

      JSON.parse(File.read(path))
    end

    # --- Writing ---------------------------------------------------------------

    # Writes one order and updates the index. Returns :written or :unchanged.
    #
    # +force+ rewrites a record we already have; without it an archived id is
    # skipped, which is what makes an incremental sync cheap.
    def write(order, force: false)
      order_id = order["order_id"].to_s
      raise ArgumentError, "an order needs an id to be archived" if order_id.empty?
      return :unchanged if archived?(order_id) && !force

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
    def note_sync(cursor: nil, reason: "complete", oldest_date: nil)
      index["sync"] = {
        "at" => timestamp,
        "reason" => reason,
        # The cursor is session-scoped and opaque. It is kept only when a walk
        # stopped early, and only so the next run can pick up where it left off.
        "cursor" => cursor,
        "oldest_date" => oldest_date
      }.compact
      save_index
      index["sync"]
    end

    def sync_state = index["sync"] || {}

    # Whether a walk has ever run against this archive.
    #
    # Distinct from complete and from resumable, because "we have never looked"
    # is a third state and the most misleading one to round off. An archive
    # that has never synced is not complete, and it is not resumable either --
    # there is nothing to resume.
    def synced? = !sync_state.empty?

    # True when the last walk ended for a reason that leaves history unseen.
    #
    # This answers "can the next run continue from here", not "is this archive
    # trustworthy". A never-synced archive is false here and must not be read
    # as complete on that basis.
    def resumable? = synced? && sync_state["reason"] != "complete"

    # True only when a walk has run and finished. The one claim a machine
    # consumer may act on.
    def complete? = synced? && sync_state["reason"] == "complete"

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
