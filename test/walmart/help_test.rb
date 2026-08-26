# frozen_string_literal: true

require "test_helper"
require "json"

module Walmart
  # `walmart order sync --help` printed the global command list, so --year,
  # --all and --full lived in the README and in a source comment and nowhere the
  # tool would tell you about. Flags a user cannot discover from the tool are
  # flags that do not exist for most users.
  class HelpTest < Minitest::Test
    def test_command_help_lists_the_flags_that_command_takes
      out, = run_cli(%w[order sync --help])

      assert_includes out, "--year"
      assert_includes out, "--all"
      assert_includes out, "--full"
    end

    def test_command_help_is_one_json_document_in_json_mode
      out, = run_cli(%w[order sync --help --json])
      document = JSON.parse(out)

      assert_equal "order sync", document["command"]
      assert_includes document["flags"].map { |flag| flag["flag"] }, "--year YYYY"
    end

    def test_a_command_with_no_flags_says_so_rather_than_printing_nothing
      out, = run_cli(%w[cart show --help])

      assert_includes out, "no command flags"
    end

    def test_help_for_an_unknown_command_falls_back_to_the_global_list
      out, = run_cli(%w[nonsense --help])

      assert_includes out, "Available now:"
    end

    def test_bare_help_is_still_the_global_list
      out, = run_cli(%w[--help])

      assert_includes out, "Available now:"
      assert_includes out, "Global flags:"
    end

    # The sixteenth command is the one written in a hurry. A command with no
    # entry here gets the global list silently, which reads like working help.
    def test_every_implemented_command_has_a_help_entry
      missing = CLI::COMMANDS.keys - Help::COMMAND_FLAGS.keys - [%w[version]]

      assert_empty missing.map { |path| path.join(" ") },
                   "these commands print the global list instead of their own flags"
    end

    def test_no_help_entry_describes_a_command_that_does_not_exist
      extra = Help::COMMAND_FLAGS.keys - CLI::COMMANDS.keys

      assert_empty(extra.map { |path| path.join(" ") })
    end

    # Help is where a user learns what the tool does, so it is also where a
    # promise about checkout would be most believed.
    def test_command_help_never_mentions_checkout
      Help::COMMAND_FLAGS.each_key do |path|
        out, = run_cli(path + ["--help"])

        Safety::FORBIDDEN_TERMS.each do |term|
          refute_includes out.downcase.gsub(/[^a-z]/, ""), term
        end
      end
    end

    # The table above is hand-written, and the first draft of it invented two
    # flags that do not exist (`config init --force`, `order list --reverse`)
    # and omitted five that do. Written from plausibility rather than from the
    # parser -- the same defect as everything else tonight, in documentation
    # form, where it is worse: a flag the tool documents and then rejects.
    #
    # So the help text is compared against what parse_flags actually accepts.
    # Per file rather than per command, because several commands share a file
    # and a finer mapping would be guesswork.
    def test_help_documents_exactly_the_flags_the_parser_accepts
      by_file.each do |file, paths|
        declared = declared_flags(file)
        documented = paths.flat_map { |path| Help::COMMAND_FLAGS.fetch(path) }
                          .filter_map { |flag, _| flag[/\A--[a-z-]+/] }
                          .map { |flag| flag.delete_prefix("--") }

        assert_equal declared.sort, documented.uniq.sort,
                     "#{file}: help and parse_flags disagree about the flags of " \
                     "#{paths.map { |path| path.join(" ") }.join(", ")}"
      end
    end

    private

    # Command paths grouped by the file implementing them.
    def by_file
      CLI::COMMANDS.reject { |_path, (klass, _)| klass.nil? }
                   .group_by { |_path, (klass, _)| Object.const_source_location(klass.name).first }
                   .transform_values { |pairs| pairs.map(&:first) }
                   .transform_keys { |path| path.delete_prefix("#{File.expand_path("../..", __dir__)}/") }
    end

    def declared_flags(file)
      source = File.read(File.expand_path("../../#{file}", __dir__))
      source.scan(/(?:valued|boolean):\s*%w\[([^\]]*)\]/).flatten.flat_map(&:split).uniq
    end

    def run_cli(argv)
      out = StringIO.new
      err = StringIO.new
      CLI.new(argv, stdout: out, stderr: err, env: {}).run
      [out.string, err.string]
    end
  end
end
