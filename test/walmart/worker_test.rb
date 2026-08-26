# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # Exercises the real subprocess/NDJSON path against a stand-in worker script,
  # so protocol bugs surface here rather than during a live Walmart session.
  class WorkerTest < TestCase
    def test_returns_the_result_payload
      worker = build_worker(<<~RUBY)
        request = JSON.parse($stdin.read)
        puts JSON.generate({"type" => "log", "level" => "info", "message" => "hi"})
        puts JSON.generate({"type" => "result", "data" => {"echo" => request["action"]}})
      RUBY

      events = []
      data = worker.run("probe") { |event| events << event }

      assert_equal({ "echo" => "probe" }, data)
      assert_equal(["hi"], events.map { |event| event["message"] })
    end

    def test_request_carries_params_options_and_secrets_on_stdin
      worker = build_worker(<<~RUBY)
        request = JSON.parse($stdin.read)
        puts JSON.generate({"type" => "result", "data" => request})
      RUBY

      data = worker.run("login", params: { manual: true }, secrets: { "password" => "p" })

      assert_equal 1, data["protocol"]
      assert_equal "login", data["action"]
      assert_equal({ "manual" => true }, data["params"])
      assert_equal({ "password" => "p" }, data["secrets"])
      assert_equal paths.browser_profile, data["options"]["profile_dir"]
    end

    # Safety invariant 8: secrets go over stdin, never argv.
    def test_secrets_never_appear_in_the_command_line
      worker = build_worker(<<~RUBY)
        puts JSON.generate({"type" => "result", "data" => {"argv" => ARGV}})
      RUBY

      data = worker.run("login", secrets: { "password" => "hunter2" })

      refute_includes data["argv"].join(" "), "hunter2"
    end

    def test_verbose_logging_redacts_the_secret_values
      logged = []
      worker = build_worker(<<~RUBY, logger: ->(_level, message) { logged << message })
        puts JSON.generate({"type" => "result", "data" => {}})
      RUBY

      worker.run("login", secrets: { "email" => "a@example.com", "password" => "hunter2" })

      joined = logged.join("\n")

      refute_includes joined, "hunter2"
      refute_includes joined, "a@example.com"
      assert_includes joined, "_keys"
    end

    def test_structured_error_becomes_a_worker_error
      worker = build_worker(<<~RUBY)
        puts JSON.generate({"type" => "error", "code" => "login_required",
                            "message" => "session expired"})
      RUBY

      error = assert_raises(WorkerError) { worker.run("cart.show") }

      assert_equal "login_required", error.code
      assert_predicate error, :login_required?
      assert_equal ExitCodes::FAILED, error.exit_code
    end

    def test_error_marked_unverified_maps_to_exit_code_three
      worker = build_worker(<<~RUBY)
        puts JSON.generate({"type" => "error", "code" => "internal",
                            "message" => "died mid-add", "unverified" => true})
      RUBY

      error = assert_raises(WorkerError) { worker.run("cart.add", mutation: true) }

      assert_equal ExitCodes::UNVERIFIED, error.exit_code
    end

    # A mutation whose worker vanishes is uncertain, not failed. Reporting it as
    # failed would invite a duplicate add.
    def test_silent_death_during_a_mutation_is_unverified
      worker = build_worker("exit 7")

      error = assert_raises(UnverifiedError) { worker.run("cart.add", mutation: true) }

      assert_equal ExitCodes::UNVERIFIED, error.exit_code
      assert_match(/may or may not/, error.message)
    end

    def test_silent_death_during_a_read_is_a_plain_failure
      worker = build_worker("exit 7")

      error = assert_raises(WorkerError) { worker.run("cart.show") }

      assert_equal "no_result", error.code
      assert_equal ExitCodes::FAILED, error.exit_code
    end

    def test_stderr_tail_is_attached_to_failures
      worker = build_worker(<<~RUBY)
        $stderr.puts "traceback line one"
        $stderr.puts "traceback line two"
        exit 1
      RUBY

      error = assert_raises(WorkerError) { worker.run("cart.show") }

      assert_includes error.details["stderr_tail"], "traceback line two"
    end

    # A crash that explains itself beats one that requires a re-run with
    # --verbose to learn anything.
    def test_stderr_tail_appears_in_the_message_without_verbose
      worker = build_worker(<<~RUBY)
        $stderr.puts "Traceback (most recent call last):"
        $stderr.puts "RuntimeError: selector not found"
        exit 1
      RUBY

      error = assert_raises(WorkerError) { worker.run("cart.show") }

      assert_includes error.message, "last worker stderr:"
      assert_includes error.message, "RuntimeError: selector not found"
    end

    # ...but a traceback can quote page text, so the tail is redacted.
    def test_the_surfaced_stderr_tail_is_redacted
      worker = build_worker(<<~RUBY)
        $stderr.puts "parse failed for account eric@example.com"
        exit 1
      RUBY

      error = assert_raises(WorkerError) { worker.run("cart.show") }

      refute_includes error.message, "eric@example.com"
      assert_includes error.message, "[REDACTED-EMAIL]"
      refute_includes error.details["stderr_tail"].join, "eric@example.com"
    end

    def test_a_clean_failure_has_no_stderr_section
      worker = build_worker("exit 1")
      error = assert_raises(WorkerError) { worker.run("cart.show") }

      refute_includes error.message, "last worker stderr:"
    end

    def test_pinned_store_is_passed_to_every_action
      config.store = { "store_id" => "100", "name" => "Test", "zip" => "00000" }
      worker = build_worker(<<~RUBY)
        request = JSON.parse($stdin.read)
        puts JSON.generate({"type" => "result", "data" => request["options"]})
      RUBY

      options = worker.run("cart.show")

      assert_equal "100", options["pinned_store"]["store_id"]
    end

    def test_python_command_prefers_an_explicit_override
      worker = Worker.new(paths: paths, config: config,
                          env: env.merge("WALMART_PYTHON" => "/opt/py3 -X dev"))

      assert_equal ["/opt/py3", "-X", "dev"], worker.python_command
    end

    def test_python_command_uses_uv_when_it_is_on_path
      bin = fake_bin("uv", "python3")
      worker = Worker.new(paths: paths, config: config, env: { "PATH" => bin })
      command = worker.python_command

      assert_equal File.join(bin, "uv"), command.first
      assert_includes command, "--project"
      assert_equal "python", command.last
    end

    def test_python_command_falls_back_to_python3
      bin = fake_bin("python3")
      worker = Worker.new(paths: paths, config: config, env: { "PATH" => bin })

      assert_equal [File.join(bin, "python3")], worker.python_command
    end

    def test_python_command_falls_back_to_python
      bin = fake_bin("python")
      worker = Worker.new(paths: paths, config: config, env: { "PATH" => bin })

      assert_equal [File.join(bin, "python")], worker.python_command
    end

    def test_no_python_at_all_is_a_named_failure
      worker = Worker.new(paths: paths, config: config, env: { "PATH" => File.join(tmp, "empty") })
      error = assert_raises(WorkerError) { worker.python_command }

      assert_equal "python_missing", error.code
    end

    def test_an_empty_override_is_ignored
      bin = fake_bin("python3")
      worker = Worker.new(paths: paths, config: config,
                          env: { "PATH" => bin, "WALMART_PYTHON" => "" })

      assert_equal [File.join(bin, "python3")], worker.python_command
    end

    private

    def fake_bin(*commands)
      dir = File.join(tmp, "bin-#{commands.join("-")}")
      FileUtils.mkdir_p(dir)
      commands.each do |command|
        path = File.join(dir, command)
        File.write(path, "#!/bin/sh\nexit 0\n")
        File.chmod(0o755, path)
      end
      dir
    end

    def build_worker(body, logger: nil)
      script = File.join(tmp, "fake_worker.rb")
      File.write(script, "require 'json'\n#{body}")
      Worker.new(paths: paths, config: config, env: env, logger: logger,
                 python_command: [RbConfig.ruby], script: script)
    end

    # Polls rather than asserting instantly: TERM, and the KILL that may follow,
    # are asynchronous.
    def assert_dead(pid, timeout: 10)
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + timeout
      while Process.clock_gettime(Process::CLOCK_MONOTONIC) < deadline
        begin
          Process.kill(0, pid)
        rescue Errno::ESRCH
          return true
        rescue Errno::EPERM
          break
        end
        sleep 0.1
      end

      flunk "worker process #{pid} was still alive; Ctrl-C or a timeout would orphan a browser"
    end
  end
end
