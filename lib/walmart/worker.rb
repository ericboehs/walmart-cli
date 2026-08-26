# frozen_string_literal: true

require "json"
require "open3"
require "timeout"
require_relative "errors"
require_relative "paths"
require_relative "redact"
require_relative "safety"
require_relative "which"
require_relative "worker_failures"
require_relative "child_process"

module Walmart
  # One-shot Python worker process speaking NDJSON.
  #
  # Protocol
  # --------
  # Ruby writes exactly one JSON object to the worker's stdin and closes it:
  #
  #   {"action":"cart.show","params":{...},"options":{...},"secrets":{...}}
  #
  # The worker writes newline-delimited JSON events to stdout:
  #
  #   {"type":"log","level":"info","message":"..."}
  #   {"type":"progress","message":"...","current":1,"total":10}
  #   {"type":"result","data":{...}}                      terminal, at most one
  #   {"type":"error","code":"login_required","message":"..."}   terminal
  #
  # stdout carries protocol only. Human diagnostics go to stderr, which we keep
  # a bounded tail of so a crash can explain itself without flooding the user.
  #
  # Secrets travel on stdin and never appear in argv, logs, or events. The
  # request is never logged verbatim; #redacted_request is what --verbose sees.
  class Worker
    include WorkerFailures
    include ChildProcess

    # Every action the worker may be asked to perform. Adding an action here is
    # a deliberate, reviewable act; safety invariant 1 forbids ever adding one
    # that submits an order. The surface-scan suite
    # (test/walmart/*_surface_test.rb) asserts this list stays clean and keeps
    # it in step with pyworker/fetch.py's ACTIONS table.
    ACTIONS = %w[
      probe
      login
      session.status
      store.show
      search
      item
      cart.show
      cart.add
      cart.remove
      my_items.list
      order.sync
      order.list
      order.show
      recon.capture
    ].freeze

    # Worker error codes the CLI reacts to specifically.
    LOGIN_CODES = %w[login_required challenge_detected session_expired].freeze

    STDERR_TAIL_LINES = 40

    def initialize(paths: Paths.new, config: nil, env: ENV, python_command: nil, script: nil,
                   logger: nil)
      @paths = paths
      @config = config
      @env = env
      @python_command = python_command
      @worker_script = script
      @logger = logger
    end

    attr_reader :paths

    # Runs one action to completion.
    #
    # Yields each non-terminal event to the block, if given, so a command can
    # render progress. Returns the result payload. Raises WorkerError on a
    # structured failure and UnverifiedError when the process dies after a
    # mutation was already in flight (the caller decides which actions those
    # are by passing mutation: true).
    def run(action, params: {}, secrets: nil, options: {}, mutation: false, timeout: nil, &block)
      assert_allowed!(action)
      request = build_request(action, params, secrets, options)
      log_request(request)

      execute(request, timeout: timeout || default_timeout, mutation: mutation, &block)
    end

    # What --verbose is allowed to print: the request with secrets replaced by
    # a list of the keys that were supplied.
    def redacted_request(request)
      copy = request.dup
      copy["secrets"] = copy["secrets"] ? { "_keys" => copy["secrets"].keys.sort } : nil
      copy
    end

    # The command line used to start the worker. No secrets, by construction.
    def python_command
      @python_command ||= detect_python_command
    end

    def worker_script
      @worker_script ||= File.expand_path("../../pyworker/fetch.py", __dir__)
    end

    def pyworker_dir
      File.expand_path("../../pyworker", __dir__)
    end

    private

    def assert_allowed!(action)
      action = action.to_s
      unless ACTIONS.include?(action)
        raise SafetyError.new("unknown worker action: #{action}", code: "unknown_action")
      end

      return if Safety.violations(action).empty?

      raise SafetyError.new("refusing a worker action that names a forbidden operation",
                            code: "forbidden_action")
    end

    def build_request(action, params, secrets, options)
      {
        "protocol" => 1,
        "action" => action.to_s,
        "params" => stringify(params || {}),
        "options" => default_options.merge(stringify(options || {})),
        "secrets" => secrets
      }
    end

    def default_options
      {
        "profile_dir" => @paths.browser_profile,
        "profile_lock" => @paths.profile_lock,
        "headless" => @config ? @config.fetch("headless", true) : true,
        "browser_channel" => @config&.fetch("browser_channel", nil),
        "cdp_url" => @config&.fetch("cdp_url", nil),
        "pinned_store" => @config&.store
      }
    end

    def default_timeout
      (@config ? @config.fetch("worker_timeout_seconds", 120) : 120).to_i
    end

    def stringify(hash)
      hash.each_with_object({}) { |(k, v), acc| acc[k.to_s] = v }
    end

    def log_request(request)
      @logger&.call(:debug, "worker request #{JSON.generate(redacted_request(request))}")
    end

    def execute(request, timeout:, mutation:, &block)
      result = nil
      failure = nil
      stderr_tail = []
      @garbled_lines = 0

      status = spawn(request, timeout: timeout, stderr_tail: stderr_tail, mutation: mutation) do |event|
        case event["type"]
        when "result" then result = event["data"]
        when "error"  then failure = event
        else block&.call(event)
        end
      end

      raise worker_error(failure, stderr_tail) if failure

      unless result.nil?
        # Protocol corruption that happened to spare the result line. Silence
        # here is how a chatty dependency's stray print becomes a mystery
        # parse failure after some later refactor moves the result.
        if @garbled_lines.positive?
          @logger&.call(:warn, "ignored #{@garbled_lines} unparseable stdout line(s) " \
                               "from a run that succeeded")
        end
        return result
      end

      raise incomplete_error(status, stderr_tail, mutation)
    end

    # Ctrl-C must not leave a Python process (and, for login, a visible
    # Chromium holding the profile lock) alive after the CLI is gone. Open3
    # closes the pipes when the block unwinds but never signals the child, so
    # the kill has to happen here, on every abnormal exit path.
    #
    # pgroup: true puts the worker in its own process group so that kill can
    # take the whole Playwright/Chromium tree with it. Without it we can only
    # signal the Python parent, and a browser outliving its owner is precisely
    # the failure the profile lock cannot detect.
    def spawn(request, timeout:, stderr_tail:, mutation:, &block)
      cmd = python_command + [worker_script]
      options = { chdir: pyworker_dir, pgroup: true }
      Open3.popen3(worker_env, *cmd, **options) do |stdin, stdout, stderr, wait_thread|
        deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + timeout
        err_thread = Thread.new { drain_stderr(stderr, stderr_tail) }
        begin
          write_request(stdin, request)
          read_events(stdout, timeout: timeout, wait_thread: wait_thread, mutation: mutation, &block)
        rescue Exception # rubocop:disable Lint/RescueException
          # Interrupt and SignalException are the reason this is not
          # StandardError. We clean up and re-raise; we never decide here that
          # the user's Ctrl-C was survivable.
          kill(wait_thread)
          raise
        ensure
          err_thread.join(2)
          err_thread.kill if err_thread.alive?
        end
        await_exit(wait_thread, deadline)
      end
    end

    def worker_env
      # A deliberately small environment addition. Nothing secret here.
      { "PYTHONUNBUFFERED" => "1", "WALMART_WORKER" => "1" }
    end

    def write_request(stdin, request)
      stdin.write(JSON.generate(request))
      stdin.write("\n")
      stdin.close
    rescue Errno::EPIPE
      # Worker died before reading; the exit status and stderr tail explain it.
      # Logged so a later investigation can tell "never received the request"
      # apart from "received it and failed".
      @logger&.call(:debug, "worker closed stdin before the request was fully written")
      nil
    end

    def read_events(stdout, timeout:, wait_thread:, mutation:)
      Timeout.timeout(timeout) do
        stdout.each_line do |line|
          line = line.strip
          next if line.empty?

          event = parse_event(line)
          next unless event

          yield event
        end
      end
    rescue Timeout::Error
      kill(wait_thread)
      raise timeout_error(timeout, mutation)
    end

    # A timed-out mutation is unverified, and it has to be the *class* that says
    # so: callers rescue UnverifiedError, and a hung cart.add that reads as a
    # plain failure is how an item gets added twice.
    def timeout_error(timeout, mutation)
      message = "worker timed out after #{timeout}s"
      if mutation
        UnverifiedError.new(
          "#{message}; the cart may or may not have changed",
          code: "timeout"
        )
      else
        WorkerError.new(message, code: "timeout", exit_code: ExitCodes::UNVERIFIED)
      end
    end

    # A mangled result line and a silent worker look identical from here, so the
    # count is carried into the eventual error rather than left to --verbose.
    # Valid JSON that is not an event object counts too: a future refactor that
    # wraps results in an array would otherwise lose them without a trace.
    def parse_event(line)
      event = JSON.parse(line)
      return event if event.is_a?(Hash)

      garbled!("worker emitted a JSON line that is not an event object; ignoring")
    rescue JSON::ParserError
      garbled!("worker emitted a non-JSON stdout line; ignoring")
    end

    def garbled!(message)
      @garbled_lines = @garbled_lines.to_i + 1
      @logger&.call(:warn, message)
      nil
    end

    def drain_stderr(stderr, tail)
      stderr.each_line do |line|
        tail << line.rstrip
        tail.shift while tail.length > STDERR_TAIL_LINES
        @logger&.call(:debug, "worker stderr: #{Redact.text(line.rstrip)}")
      end
    rescue IOError
      nil
    end

    def detect_python_command
      override = @env["WALMART_PYTHON"]
      return override.split if override && !override.empty?

      uv = Which.find("uv", env: @env)
      return [uv, "run", "--quiet", "--project", pyworker_dir, "python"] if uv

      python = Which.find("python3", env: @env) || Which.find("python", env: @env)
      raise WorkerError.new("no python3 found on PATH", code: "python_missing") unless python

      [python]
    end
  end
end
