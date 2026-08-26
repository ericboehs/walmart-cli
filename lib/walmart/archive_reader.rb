# frozen_string_literal: true

module Walmart
  # Turning an index entry into a record, or refusing to.
  #
  # Split from Archive because it is the part with an adversary. Everything here
  # exists because the index is a file on disk and the record files are files on
  # disk, and neither is guaranteed to be what the writer left behind: entries
  # can point outside the archive, files can be symlinks, JSON can be truncated,
  # a record can hold another order, and a format can be newer than this CLI
  # understands. The rule throughout is that a refusal names what is wrong and
  # never sanitizes: nothing unsafe is briefly a candidate.
  module ArchiveReader
    # Archive::FORMAT rather than FORMAT: this module is included into Archive,
    # so the constant is in that class's lexical scope and not in this file's.
    # It resolves at call time.

    private

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
      return unless format > Archive::FORMAT

      raise ArchiveIncompatibleError.new(
        "order #{id} was written by a newer version of walmart-cli (format #{format}); " \
        "this one understands format #{Archive::FORMAT}",
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
  end
end
