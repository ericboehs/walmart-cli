# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # What happens when the worker does not finish cleanly: timeouts, Ctrl-C, a
  # child that ignores TERM, a child that never reads its request.
  #
  # These are the paths that decide whether an aborted command leaves a browser
  # running, and whether an interrupted cart change is reported as "failed"
  # (safe to retry) or "unverified" (check before retrying).
  class WorkerLifecycleTest < TestCase
    # A short grace, not no grace. These tests prove TERM is followed by KILL
    # and that the wait between them is real; at the production five seconds
    # they spent seventeen seconds of every suite run sitting still, which a
    # mutation run pays for once per mutant. 0.3s is long enough that a process
    # honouring TERM still wins the race on a loaded machine.
    FAST_GRACE = 0.3

    # The children here sleep for thirty seconds or more, so any budget short
    # of that times out; the number only decides how long the test waits to
    # watch it happen.
    FAST_TIMEOUT = 0.3

    def test_timeout_is_reported_as_unverified_rather_than_hanging
      worker = build_worker(<<~RUBY)
        sleep 30
      RUBY

      error = assert_raises(WorkerError) { worker.run("probe", timeout: FAST_TIMEOUT) }

      assert_equal "timeout", error.code
      assert_equal ExitCodes::UNVERIFIED, error.exit_code
    end

    # The exit code was already right; the class was not. Callers rescue
    # UnverifiedError, and a hung cart.add that arrives as a plain WorkerError
    # is how an item gets added twice.
    def test_a_timeout_during_a_mutation_raises_unverified_error
      worker = build_worker(<<~RUBY)
        sleep 30
      RUBY

      error = assert_raises(UnverifiedError) { worker.run("cart.add", mutation: true, timeout: FAST_TIMEOUT) }

      assert_equal "timeout", error.code
      assert_equal ExitCodes::UNVERIFIED, error.exit_code
      assert_match(/may or may not have changed/, error.message)
    end

    def test_a_timeout_kills_the_worker_instead_of_leaving_it_running
      marker = File.join(tmp, "pid")
      worker = build_worker(<<~RUBY)
        File.write(#{marker.inspect}, Process.pid.to_s)
        sleep 30
      RUBY

      assert_raises(WorkerError) { worker.run("probe", timeout: FAST_TIMEOUT) }

      assert_dead(File.read(marker).to_i)
    end

    # A worker that ignores TERM must not hang the CLI past its own timeout.
    # The worker is not a leaf. It drives Playwright, which runs Chromium, and
    # a grandchild that survives holds Chromium's own lock inside the profile
    # while our flock reports it free -- so the next command fails inside
    # Playwright instead of at the guard that has a good error message.
    def test_a_timeout_kills_the_whole_process_tree_not_just_the_worker
      parent_marker = File.join(tmp, "parent-pid")
      child_marker = File.join(tmp, "child-pid")
      worker = build_worker(<<~RUBY)
        child = spawn(#{RbConfig.ruby.inspect}, "-e", <<~CHILD)
          File.write(#{child_marker.inspect}, Process.pid.to_s)
          sleep 120
        CHILD
        File.write(#{parent_marker.inspect}, Process.pid.to_s)
        Process.detach(child)
        sleep 120
      RUBY

      assert_raises(WorkerError) { worker.run("probe", timeout: FAST_TIMEOUT) }
      wait_for_file(child_marker)

      assert_dead(File.read(parent_marker).to_i)
      assert_dead(File.read(child_marker).to_i)
    end

    # A stray grandchild is the one failure with no `walmart` process in ps for
    # the user to find, so the group is what has to die -- and the group is
    # only ours to kill because the spawn asked for one.
    def test_the_worker_gets_its_own_process_group
      marker = File.join(tmp, "pgid")
      worker = build_worker(<<~RUBY)
        File.write(#{marker.inspect}, [Process.pid, Process.getpgrp].join(" "))
        puts({ "type" => "result", "ok" => true, "data" => {} }.to_json)
      RUBY

      worker.run("probe", timeout: 10)
      pid, pgid = File.read(marker).split.map(&:to_i)

      assert_equal pid, pgid, "the worker should lead its own group"
      refute_equal Process.getpgrp, pgid, "the worker must not share our group"
    end

    def test_a_worker_that_ignores_term_is_killed
      marker = File.join(tmp, "pid")
      worker = build_worker(<<~RUBY)
        Signal.trap("TERM") { }
        File.write(#{marker.inspect}, Process.pid.to_s)
        sleep 60
      RUBY

      assert_raises(WorkerError) { worker.run("probe", timeout: FAST_TIMEOUT) }

      assert_dead(File.read(marker).to_i)
    end

    # Ctrl-C during a login would otherwise leave a visible Chromium holding
    # the profile lock, and the next command would blame a ghost.
    def test_an_interrupt_kills_the_worker_before_it_propagates
      marker = File.join(tmp, "pid")
      worker = build_worker(<<~RUBY)
        File.write(#{marker.inspect}, Process.pid.to_s)
        puts JSON.generate({"type" => "progress", "message" => "started"})
        $stdout.flush
        sleep 60
      RUBY

      assert_raises(Interrupt) do
        worker.run("login") { |_event| raise Interrupt }
      end

      assert_dead(File.read(marker).to_i)
      refute_path_exists paths.profile_lock
    end

    # The rescue assumes "the exit status and stderr tail explain it"; this
    # proves a worker that never reads stdin yields a structured error rather
    # than a raw Errno::EPIPE out of the CLI.
    def test_a_worker_that_closes_stdin_immediately_fails_structurally
      worker = build_worker(<<~RUBY)
        $stdin.close
        $stderr.puts "refused to read the request"
        sleep 0.2
        exit 9
      RUBY

      error = assert_raises(WorkerError) { worker.run("probe") }

      assert_equal "no_result", error.code
      assert_equal 9, error.details["exit_status"]
    end

    def test_a_worker_that_closes_stdin_during_a_mutation_is_unverified
      worker = build_worker(<<~RUBY)
        $stdin.close
        sleep 0.2
        exit 9
      RUBY

      assert_raises(UnverifiedError) { worker.run("cart.add", mutation: true) }
    end

    # Valid JSON that is not an event object would otherwise vanish silently.
    def test_json_that_is_not_an_event_object_is_counted
      worker = build_worker(<<~RUBY)
        puts JSON.generate([{"type" => "result", "data" => {}}])
        puts JSON.generate("just a string")
        exit 0
      RUBY

      error = assert_raises(WorkerError) { worker.run("cart.show") }

      assert_equal 2, error.details["garbled_stdout_lines"]
    end

    # A worker that delivers its result and then wedges in browser cleanup used
    # to hang here forever, past whatever timeout the user configured.
    def test_a_worker_that_hangs_after_its_result_is_not_waited_on_forever
      worker = build_worker(<<~RUBY)
        puts JSON.generate({"type" => "result", "data" => {"ok" => true}})
        $stdout.flush
        # Releases the pipe (EOF for the parent) without exiting: this is the
        # wedged-cleanup shape, a worker done talking but not done running.
        $stdout.reopen(IO::NULL)
        sleep 60
      RUBY

      started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
      result = nil
      Timeout.timeout(30) { result = worker.run("cart.show", timeout: FAST_TIMEOUT) }
      elapsed = Process.clock_gettime(Process::CLOCK_MONOTONIC) - started

      # The result was delivered, so it still counts; only the wedged cleanup
      # was cut short.
      assert_equal({ "ok" => true }, result)
      assert_operator elapsed, :<, 20
    end

    def test_a_worker_that_hangs_after_its_result_is_killed
      marker = File.join(tmp, "pid")
      worker = build_worker(<<~RUBY)
        File.write(#{marker.inspect}, Process.pid.to_s)
        puts JSON.generate({"type" => "result", "data" => {"ok" => true}})
        $stdout.flush
        # Releases the pipe (EOF for the parent) without exiting: this is the
        # wedged-cleanup shape, a worker done talking but not done running.
        $stdout.reopen(IO::NULL)
        sleep 60
      RUBY

      Timeout.timeout(30) { worker.run("cart.show", timeout: FAST_TIMEOUT) }

      assert_dead(File.read(marker).to_i)
    end

    private

    def build_worker(body, logger: nil)
      script = File.join(tmp, "fake_worker.rb")
      File.write(script, "require 'json'\n#{body}")
      worker = Worker.new(paths: paths, config: config, env: env, logger: logger,
                          python_command: [RbConfig.ruby], script: script)
      worker.define_singleton_method(:kill_grace_seconds) { FAST_GRACE }
      worker.define_singleton_method(:exit_grace_seconds) { FAST_GRACE }
      worker
    end

    def wait_for_file(path, timeout: 10)
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + timeout
      sleep 0.05 while !File.exist?(path) && Process.clock_gettime(Process::CLOCK_MONOTONIC) < deadline
      path
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
