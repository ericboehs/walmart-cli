# frozen_string_literal: true

require "test_helper"

module Walmart
  module Commands
    # `walmart browser start|status`.
    #
    # The command itself is thin. What matters is that it points the user at a
    # profile Chrome will actually accept a debugging port on, and that its
    # printed instructions name commands that exist -- both of which have gone
    # wrong tonight.
    class BrowserTest < TestCase
      def test_status_reports_a_dead_port_as_a_failure_not_a_crash
        code = run_browser(%w[browser status --port 1])

        assert_equal ExitCodes::FAILED, code
        assert_match(/nothing is listening/, err.string + out.string)
      end

      # A port nobody is serving must not read as "running" merely because the
      # request failed in an interesting way.
      def test_a_closed_port_is_not_mistaken_for_a_browser
        code = run_browser(%w[browser status --port 1 --json])
        payload = JSON.parse(out.string)

        assert_equal "not_running", payload["state"]
        assert_nil payload["browser"]
        assert_equal ExitCodes::FAILED, code
      end

      def test_the_cdp_url_is_always_loopback
        run_browser(%w[browser status --port 4321 --json])

        assert_equal "http://127.0.0.1:4321", JSON.parse(out.string)["cdp_url"]
      end

      def test_an_unknown_subcommand_is_a_usage_error
        assert_raises(UsageError) { run_browser(%w[browser teleport]) }
      end

      def test_a_missing_subcommand_is_a_usage_error
        assert_raises(UsageError) { run_browser(%w[browser]) }
      end

      def test_a_port_that_is_not_a_number_is_refused_before_anything_opens
        error = assert_raises(UsageError) { run_browser(%w[browser status --port yes]) }

        assert_match(/TCP port/, error.message)
      end

      # Chrome 136+ refuses --remote-debugging-port on the default profile. If
      # this ever points at the user's real profile, Chrome silently ignores the
      # port and `browser start` appears to work while nothing can attach.
      def test_the_profile_is_ours_and_not_the_users_everyday_chrome
        command = build_browser
        profile = command.send(:profile_path)

        assert_includes profile, tmp
        refute_includes profile, "Library/Application Support/Google/Chrome"
      end

      def test_the_launch_arguments_carry_the_port_and_the_separate_profile
        command = build_browser
        spawned = []
        command.define_singleton_method(:spawn_chrome) do |chrome, port, profile|
          spawned << [chrome, port, profile]
          1234
        end
        command.define_singleton_method(:chrome_binary) { "/bin/true" }
        command.define_singleton_method(:endpoint_version) { |_port| nil }

        command.run(%w[browser start], %w[--port 9333])

        chrome, port, profile = spawned.fetch(0)

        assert_equal "/bin/true", chrome
        assert_equal 9333, port
        assert_includes profile, "attached-chrome"
      end

      # The profile holds a live Walmart session.
      def test_the_profile_directory_is_created_private
        command = build_browser
        command.define_singleton_method(:spawn_chrome) { |*| 1 }
        command.define_singleton_method(:chrome_binary) { "/bin/true" }
        command.define_singleton_method(:endpoint_version) { |_port| nil }

        command.run(%w[browser start], [])

        assert_equal "700", format("%o", File.stat(command.send(:profile_path)).mode & 0o777)
      end

      def test_a_missing_chrome_is_reported_rather_than_spawned
        command = build_browser
        command.define_singleton_method(:chrome_binary) { nil }
        command.define_singleton_method(:endpoint_version) { |_port| nil }

        assert_raises(Walmart::Error) { command.run(%w[browser start], []) }
      end

      # Starting a second Chrome on a port already serving one would fail
      # confusingly, and the user's signed-in window is the one that matters.
      def test_start_does_not_relaunch_over_a_running_browser
        command = build_browser
        command.define_singleton_method(:endpoint_version) { |_port| "Chrome/151" }
        command.define_singleton_method(:spawn_chrome) { |*| flunk("must not relaunch") }

        code = command.run(%w[browser start], [])

        assert_equal ExitCodes::OK, code
        assert_match(/already listening/, out.string + err.string)
      end

      private

      def build_browser(json: false)
        Browser.new(build_context(json: json))
      end

      def run_browser(argv)
        json = argv.include?("--json")
        path = argv.take_while { |word| !word.start_with?("-") }
        args = argv.drop(path.length) - ["--json"]
        build_browser(json: json).run(path, args)
      end
    end
  end
end
