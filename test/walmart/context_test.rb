# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # The Context is where a broken config first meets a running command.
  class ContextTest < TestCase
    def test_verbose_logging_is_off_by_default
      context = build_context

      context.log(:warn, "something happened")

      assert_empty context.formatter.err.string
    end

    def test_verbose_logging_reaches_stderr
      context = build_context(options: { verbose: true })

      context.log(:warn, "something happened")

      assert_includes context.formatter.err.string, "[warn] something happened"
    end

    def test_json_mode_is_never_interactive
      context = build_context(json: true, stdin: tty(StringIO.new("y\n")))

      refute_predicate context, :interactive?
    end

    # An invalid config must not silently decide the color mode for the rest of
    # the user's life. Every other path raises loudly; this one at least says so.
    def test_an_invalid_config_warns_before_falling_back_to_auto
      warnings = StringIO.new
      context = Context.new(options: {}, paths: paths, env: env, stderr: warnings,
                            config: broken_config)

      assert_equal "auto", context.color_mode
      assert_includes warnings.string, "warning:"
      assert_includes warnings.string, "using color mode 'auto'"
    end

    def test_the_config_warning_is_printed_once
      warnings = StringIO.new
      context = Context.new(options: {}, paths: paths, env: env, stderr: warnings,
                            config: broken_config)

      3.times { context.color_mode }

      assert_equal 1, warnings.string.lines.count
    end

    def test_no_color_wins_without_reading_the_config
      context = Context.new(options: { no_color: true }, paths: paths, env: env,
                            stderr: StringIO.new, config: broken_config)

      assert_equal "never", context.color_mode
    end

    private

    def broken_config
      FileUtils.mkdir_p(File.dirname(paths.config_file))
      File.write(paths.config_file, JSON.generate({ "color" => "rainbow" }))
      Config.new(paths: paths)
    end

    def tty(io)
      def io.tty? = true
      io
    end
  end
end
