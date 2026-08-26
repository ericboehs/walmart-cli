# frozen_string_literal: true

require_relative "errors"
require_relative "redact"

module Walmart
  # How a failed worker run becomes an exception.
  #
  # Split out of Worker because the rules here are their own subject: which
  # failures are merely failed (exit 1) and which are *unverified* (exit 3),
  # and how much of the worker's stderr a user is allowed to see. Getting the
  # second question wrong leaks; getting the first wrong makes someone re-add
  # an item that was already added.
  module WorkerFailures
    private

    def worker_error(event, stderr_tail)
      code = event["code"].to_s
      code = "worker_error" if code.empty?
      message = event["message"].to_s
      message = "worker failed (#{code})" if message.empty?
      WorkerError.new(
        with_stderr(message, stderr_tail),
        code: code,
        exit_code: event["unverified"] ? ExitCodes::UNVERIFIED : ExitCodes::FAILED,
        details: (event["details"] || {}).merge("stderr_tail" => Redact.tail(stderr_tail, limit: 10))
      )
    end

    def incomplete_error(status, stderr_tail, mutation)
      detail = {
        "exit_status" => status&.exitstatus,
        "stderr_tail" => Redact.tail(stderr_tail, limit: 10),
        "garbled_stdout_lines" => @garbled_lines.to_i
      }
      if mutation
        UnverifiedError.new(
          with_stderr(
            "worker exited before confirming the result; the cart may or may not have changed",
            stderr_tail
          ),
          details: detail
        )
      else
        WorkerError.new(
          with_stderr(no_result_message(status), stderr_tail),
          code: "no_result",
          details: detail
        )
      end
    end

    def no_result_message(status)
      message = "worker exited without a result (status #{status&.exitstatus.inspect})"
      return message if @garbled_lines.to_i.zero?

      "#{message}; #{@garbled_lines} unparseable stdout line(s) were ignored"
    end

    # A crash that explains itself beats a crash that requires re-running with
    # --verbose to learn anything. The tail is short and redacted, because
    # tracebacks can quote page text.
    def with_stderr(message, stderr_tail)
      tail = Redact.tail(stderr_tail, limit: 3).reject(&:empty?)
      return message if tail.empty?

      "#{message}\nlast worker stderr:\n#{tail.map { |line| "  #{line}" }.join("\n")}"
    end
  end
end
