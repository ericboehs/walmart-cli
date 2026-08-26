# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  class CLITest < TestCase
    def test_version
      code = CLI.run(["version"], stdout: out, stderr: err, env: env)

      assert_equal ExitCodes::OK, code
      assert_match(/walmart-cli \d+\.\d+\.\d+/, out.string)
    end

    def test_version_json
      CLI.run(["version", "--json"], stdout: out, stderr: err, env: env)

      assert_equal "walmart-cli", stdout_json["name"]
    end

    def test_help_lists_available_and_planned_commands
      CLI.run(["help"], stdout: out, stderr: err, env: env)

      assert_includes out.string, "doctor"
      assert_includes out.string, "Planned (not yet implemented)"
      assert_includes out.string, "cart add"
    end

    def test_bare_invocation_prints_help
      assert_equal ExitCodes::OK, CLI.run([], stdout: out, stderr: err, env: env)
      assert_includes out.string, "Global flags"
    end

    def test_unknown_command_is_a_usage_error
      code = CLI.run(["teleport"], stdout: out, stderr: err, env: env)

      assert_equal ExitCodes::USAGE, code
      assert_includes err.string, "unknown command: teleport"
    end

    def test_longest_command_prefix_wins
      context = build_context
      code = CLI.new(%w[config path], stdout: out, stderr: err, env: env,
                                      context: context).run

      assert_equal ExitCodes::OK, code
      assert_includes context.formatter.out.string, paths.config_file
    end

    def test_global_flags_are_accepted_anywhere_in_argv
      cli = CLI.new(["--json", "version"], stdout: out, stderr: err, env: env)
      cli.run

      assert_equal "walmart-cli", JSON.parse(out.string)["name"]
    end

    def test_config_init_creates_the_file
      context = build_context
      CLI.new(%w[config init], stdout: out, stderr: err, env: env, context: context).run

      assert_path_exists paths.config_file
      assert_includes context.formatter.out.string, "created"
    end

    def test_config_show_json_includes_paths
      context = build_context(json: true)
      CLI.new(["config", "show", "--json"], stdout: out, stderr: err, env: env,
                                            context: context).run
      payload = JSON.parse(context.formatter.out.string)

      assert_equal paths.config_file, payload["paths"]["config_file"]
      assert_equal true, payload["config"]["headless"]
    end

    # --- The --json contract ----------------------------------------------------
    #
    # `--json` promises one complete machine-readable document. The promise has
    # to hold on the paths nobody demos, because a consumer pipes stdout to a
    # parser and a zero-byte stdout fails inside the parser rather than at the
    # check they wrote.

    def test_an_error_is_a_json_document_in_json_mode
      context = build_context(json: true)
      code = CLI.new(%w[order show 404 --json], stdout: out, stderr: err, env: env,
                                                context: context).run
      payload = JSON.parse(context.formatter.out.string)

      assert_equal ExitCodes::FAILED, code
      assert_equal "not_found", payload.dig("error", "code")
    end

    def test_an_unknown_command_is_a_json_document_in_json_mode
      context = build_context(json: true)
      CLI.new(%w[teleport --json], stdout: out, stderr: err, env: env, context: context).run
      payload = JSON.parse(context.formatter.out.string)

      assert_equal "unknown_command", payload.dig("error", "code")
    end

    def test_an_internal_error_is_still_a_json_document
      context = build_context(json: true)
      def context.paths = raise("a bug, not a known failure mode")

      code = CLI.new(%w[order list --json], stdout: out, stderr: err, env: env,
                                            context: context).run
      payload = JSON.parse(context.formatter.out.string)

      assert_equal ExitCodes::FAILED, code
      assert_equal "internal_error", payload.dig("error", "code")
    end

    # Help goes to stdout too, so in JSON mode it cannot be prose.
    def test_help_is_a_json_document_in_json_mode
      context = build_context(json: true)
      CLI.new(%w[--json], stdout: out, stderr: err, env: env, context: context).run
      payload = JSON.parse(context.formatter.out.string)

      assert_equal false, payload["checks_out"]
      assert_includes payload["commands"].map { |row| row["command"] }, "order sync"
    end

    # One document, not two. A second would concatenate into something that is
    # neither JSON nor NDJSON, and the caller would read it as our data being
    # wrong rather than our output being malformed.
    def test_an_error_after_a_document_does_not_emit_a_second_one
      context = build_context(json: true)
      context.formatter.emit_json("already" => true)
      CLI.new(%w[teleport --json], stdout: out, stderr: err, env: env, context: context).run

      assert_equal 1, context.formatter.out.string.scan(/^\{$/).length
    end

    def test_the_error_document_carries_no_secrets
      context = build_context(json: true)
      def context.paths = raise("password=hunter2 leaked")

      CLI.new(%w[order list --json], stdout: out, stderr: err, env: env, context: context).run

      refute_includes context.formatter.out.string, "hunter2"
    end

    def test_a_config_error_exits_with_the_usage_code
      FileUtils.mkdir_p(File.dirname(paths.config_file))
      File.write(paths.config_file, "{broken")
      # No injected context here: this must exercise the real load path.
      code = CLI.new(%w[config show], stdout: out, stderr: err, env: env).run

      assert_equal ExitCodes::USAGE, code
      assert_includes err.string, "not valid JSON"
    end

    def test_an_invalid_setting_in_the_file_fails_on_load
      FileUtils.mkdir_p(File.dirname(paths.config_file))
      File.write(paths.config_file, JSON.generate({ "headless" => "false" }))
      code = CLI.new(%w[config show], stdout: out, stderr: err, env: env).run

      assert_equal ExitCodes::USAGE, code
      assert_includes err.string, "headless must be true or false"
    end

    # `walmart login logout` used to sign in while the user believed they had
    # signed out. A stray word is a typo, and typos here are expensive.
    def test_a_stray_word_after_a_command_is_refused
      context = build_context
      code = CLI.new(%w[login logout], stdout: out, stderr: err, env: env, context: context).run

      assert_equal ExitCodes::USAGE, code
      assert_includes err.string, "unexpected argument: logout"
    end

    def test_a_stray_word_after_config_show_is_refused
      context = build_context
      code = CLI.new(%w[config show garbage], stdout: out, stderr: err, env: env,
                                              context: context).run

      assert_equal ExitCodes::USAGE, code
      assert_includes err.string, "unexpected argument: garbage"
    end

    # A bug still has to leave through the documented exit codes rather than as
    # a raw Ruby backtrace.
    def test_an_unexpected_exception_becomes_a_reported_internal_error
      context = build_context
      context.define_singleton_method(:config) { raise TypeError, "boom" }
      code = CLI.new(%w[config show], stdout: out, stderr: err, env: env, context: context).run

      assert_equal ExitCodes::FAILED, code
      assert_includes err.string, "internal error: TypeError: boom"
      assert_includes err.string, "--verbose"
    end

    def test_an_internal_error_message_is_redacted
      context = build_context
      context.define_singleton_method(:config) { raise TypeError, "bad token for eric@example.com" }
      CLI.new(%w[config show], stdout: out, stderr: err, env: env, context: context).run

      refute_includes err.string, "eric@example.com"
      assert_includes err.string, "[REDACTED-EMAIL]"
    end

    def test_verbose_shows_a_backtrace_instead_of_the_hint
      context = build_context(options: { verbose: true })
      context.define_singleton_method(:config) { raise TypeError, "boom" }
      CLI.new(%w[config show --verbose], stdout: out, stderr: err, env: env,
                                         context: context).run

      assert_includes err.string, "cli_test.rb"
      refute_includes err.string, "run again with --verbose"
    end

    # Ctrl-C is not a bug; it must not be swallowed by the internal-error path.
    def test_an_interrupt_is_reported_as_aborted
      context = build_context
      context.define_singleton_method(:config) { raise Interrupt }
      code = CLI.new(%w[config show], stdout: out, stderr: err, env: env, context: context).run

      assert_equal ExitCodes::USAGE, code
      assert_includes err.string, "aborted"
      refute_includes err.string, "internal error"
    end
  end
end
