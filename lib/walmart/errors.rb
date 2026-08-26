# frozen_string_literal: true

module Walmart
  # Process exit codes, shared by every command.
  #
  # DESIGN.md fixes these meanings for cart mutations; read commands use the
  # same vocabulary so a caller never has to special-case a subcommand.
  module ExitCodes
    OK           = 0 # completed, and verified where verification is required
    FAILED       = 1 # rejected by Walmart, or verification proved it did not happen
    USAGE        = 2 # usage error, declined prompt, missing --confirm, unsafe request
    UNVERIFIED   = 3 # may have happened; verification could not be completed
  end

  # Base class for everything this CLI raises on purpose.
  class Error < StandardError
    def initialize(message, code: nil, exit_code: ExitCodes::FAILED, details: nil)
      super(message)
      @code = code
      @exit_code = exit_code
      @details = details || {}
    end

    attr_reader :code, :exit_code, :details
  end

  # Bad flags, missing arguments, declined prompts, refused-because-unsafe.
  class UsageError < Error
    def initialize(message, code: "usage", details: nil)
      super(message, code: code, exit_code: ExitCodes::USAGE, details: details)
    end
  end

  # A safety invariant said no. Never downgrade one of these to a warning.
  class SafetyError < UsageError
    def initialize(message, code: "unsafe", details: nil)
      super
    end
  end

  # The Python worker reported a structured failure.
  class WorkerError < Error
    def initialize(message, code: "worker_error", exit_code: ExitCodes::FAILED, details: nil)
      super
    end

    # The worker says the session is gone; tell the user how to fix it. The
    # code list lives in Walmart::Worker so there is one place to update.
    def login_required?
      Worker::LOGIN_CODES.include?(code)
    end
  end

  # We asked the worker to do something and never learned whether it happened.
  class UnverifiedError < Error
    def initialize(message, code: "unverified", details: nil)
      super(message, code: code, exit_code: ExitCodes::UNVERIFIED, details: details)
    end
  end

  # An archived record exists in the index and cannot be returned: the file is
  # missing, unparseable, holds a different order, or the entry points outside
  # the archive. Distinct from a genuinely unknown id, which is not damage and
  # must not be reported as though it were.
  class ArchiveError < Error
    def initialize(message, code: "archive_unreadable", details: nil)
      super(message, code: code, exit_code: ExitCodes::FAILED, details: details)
    end
  end

  # An archived record was written by a newer version of this CLI. Not damage,
  # and emphatically not repairable: we cannot read it, so we must not write
  # over it either. Separate from ArchiveError so no repair path can catch it
  # by accident.
  class ArchiveIncompatibleError < Error
    def initialize(message, code: "archive_incompatible", details: nil)
      super(message, code: code, exit_code: ExitCodes::FAILED, details: details)
    end
  end

  # Stored configuration or archive data is unreadable or malformed.
  class ConfigError < Error
    def initialize(message, code: "config", details: nil)
      super(message, code: code, exit_code: ExitCodes::USAGE, details: details)
    end
  end
end
