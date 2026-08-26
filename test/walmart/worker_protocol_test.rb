# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # The stdout/stdin contract with the worker: one JSON request in, NDJSON
  # events out. What matters here is that noise on the channel is never
  # mistaken for silence, and never silently discarded either.
  class WorkerProtocolTest < TestCase
    # Corruption that happened to spare the result line is still corruption:
    # the next refactor that moves which line carries the result would fail
    # opaquely, with nothing in the record saying stdout had been noisy.
    def test_garbled_lines_are_reported_even_when_the_run_succeeds
      logged = []
      worker = build_worker(<<~RUBY, logger: ->(level, message) { logged << [level, message] })
        puts "a chatty dependency printed this"
        puts JSON.generate({"type" => "result", "data" => {"ok" => true}})
      RUBY

      assert_equal({ "ok" => true }, worker.run("cart.show"))

      warning = logged.filter_map { |level, message| message if level == :warn }.join("\n")

      assert_match(/ignored 1 unparseable stdout line/, warning)
    end

    def test_a_clean_run_says_nothing_about_garbled_lines
      logged = []
      worker = build_worker(<<~RUBY, logger: ->(level, message) { logged << [level, message] })
        puts JSON.generate({"type" => "result", "data" => {"ok" => true}})
      RUBY

      worker.run("cart.show")

      refute_includes logged.map(&:last).join("\n"), "unparseable"
    end

    # Whether the request was ever received is the first question when a worker
    # fails, and the answer used to be nowhere.
    def test_a_worker_that_never_reads_the_request_leaves_a_trace_in_the_log
      logged = []
      worker = build_worker(<<~RUBY, logger: ->(level, message) { logged << [level, message] })
        $stdin.close
        sleep 0.2
        exit 1
      RUBY
      big = { "padding" => "x" * 200_000 }

      assert_raises(WorkerError) { worker.run("cart.show", params: big) }
      assert_includes logged.map(&:last).join("\n"), "closed stdin"
    end

    def test_non_json_stdout_noise_is_ignored
      worker = build_worker(<<~RUBY)
        puts "this is not json"
        puts JSON.generate({"type" => "result", "data" => {"ok" => true}})
      RUBY

      assert_equal({ "ok" => true }, worker.run("probe"))
    end

    # A mangled result line and a silent worker look identical from the user's
    # side; the count makes the difference visible without --verbose.
    def test_unparseable_stdout_lines_are_counted_in_the_failure
      worker = build_worker(<<~RUBY)
        puts "not json at all"
        puts "still not json"
        exit 0
      RUBY

      error = assert_raises(WorkerError) { worker.run("cart.show") }

      assert_equal 2, error.details["garbled_stdout_lines"]
      assert_match(/2 unparseable stdout line/, error.message)
    end

    private

    def build_worker(body, logger: nil)
      script = File.join(tmp, "fake_worker.rb")
      File.write(script, "require 'json'\n#{body}")
      Worker.new(paths: paths, config: config, env: env, logger: logger,
                 python_command: [RbConfig.ruby], script: script)
    end
  end
end
