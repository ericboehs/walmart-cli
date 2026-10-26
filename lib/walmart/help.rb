# frozen_string_literal: true

module Walmart
  # The help text, in both of the forms it has to take.
  #
  # Lifted out of CLI because it is the one part of the dispatcher that is
  # purely about wording: a person's screen and a script's parser describing the
  # same command list, which have to stay in step with each other and with
  # COMMANDS. Mixed in rather than called, so it keeps reading the same
  # constants and writing to the same streams.
  #
  # The command tables are named CLI:: explicitly: constant lookup in a mixin
  # is lexical, so a bare COMMANDS here resolves to Walmart::Help::COMMANDS and
  # fails at runtime rather than at load.
  module Help
    # Per-command flags, for `walmart order sync --help`.
    #
    # Written here rather than in each command because help is a wording
    # concern and this is where the wording lives -- and because a table has a
    # completeness test, while a method per command has nothing that notices
    # the one nobody wrote. `order sync` is the entry that motivated it: --year,
    # --all and --full were documented in the README and in a comment, and the
    # tool itself would not say them.
    COMMAND_FLAGS = {
      %w[order sync] => [
        ["--year YYYY", "sync one calendar year (2000-2100)"],
        ["--all", "sync everything Walmart still shows, not the default two years"],
        ["--full", "re-read and rewrite orders already archived"]
      ],
      %w[order list] => [
        ["--limit N", "show at most N orders"],
        ["--year YYYY", "only orders from one calendar year"],
        ["--all", "no limit"]
      ],
      %w[order show] => [["<order-id>", "the id printed by `walmart order list`"]],
      %w[order search] => [
        ["<text>", "match item names in archived orders"],
        ["--limit N", "show at most N orders"],
        ["--all", "no limit"]
      ],
      %w[search] => [
        ["<text>", "what to search Walmart's catalog for"],
        ["--limit N", "show at most N results"],
        ["--page N", "results page to read"],
        ["--marketplace", "include marketplace sellers, not only Walmart's own"]
      ],
      %w[item] => [["<id-or-url>", "a usItemId or a walmart.com/ip/... URL"]],
      %w[login] => [
        ["--wait N", "seconds to wait for the sign-in to complete"],
        ["--manual", "sign in by hand rather than filling stored credentials"],
        ["--force", "sign in again even if a session already looks valid"]
      ],
      %w[logout] => [],
      %w[config show] => [],
      %w[config set] => [
        ["<setting> <value>", "one setting from `walmart config show`"]
      ],
      %w[config unset] => [["<setting>", "restore one setting to its default"]],
      %w[browser start] => [
        ["--port N", "debugging port to listen on (default 9222)"]
      ],
      %w[browser status] => [["--port N", "debugging port to check (default 9222)"]],
      %w[config path] => [],
      %w[config init] => [],
      %w[cart show] => [],
      %w[cart add] => [
        ["<item-id> [n]", "add n (default 1); a negative n takes that many out"]
      ],
      %w[cart remove] => [["<item-id>", "take the whole line out of the cart"]],
      %w[store show] => [],
      %w[store pin] => [],
      %w[doctor] => [],
      %w[credentials] => []
    }.freeze

    private

    def print_command_help(words, code)
      path = help_topic(words)
      return print_help(code) unless path

      return print_command_help_json(path, code) if json?

      summary = CLI::COMMANDS.dig(path, 1)
      flags = COMMAND_FLAGS.fetch(path)
      @stdout.puts("walmart #{path.join(" ")} - #{summary}\n\n")
      @stdout.puts(flags.empty? ? "  no command flags\n\n" : "#{flag_lines(flags)}\n\n")
      @stdout.puts("Global flags are listed by `walmart help`.")
      code
    end

    def print_command_help_json(path, code)
      context(@options).formatter.emit_json(
        "command" => path.join(" "),
        "summary" => CLI::COMMANDS.dig(path, 1),
        "flags" => COMMAND_FLAGS.fetch(path).map do |flag, description|
          { "flag" => flag, "summary" => description }
        end
      )
      code
    end

    def print_help(code)
      return print_help_json(code) if json?

      @stdout.puts(<<~HELP)
        walmart #{VERSION} - build a Walmart grocery delivery cart from the command line

        This tool never checks out. Review substitutions, the delivery slot, tip,
        payment, and the final order in Walmart's app.

        Available now:
        #{command_lines(CLI::COMMANDS.map { |path, (_h, summary)| [path, summary] })}

        Planned (not yet implemented):
        #{command_lines(CLI::PLANNED.to_a)}

        Global flags:
          --json        one complete JSON document, including on errors
          -q, --quiet   suppress non-essential status output
          -v, --verbose diagnostic logs (never secrets)
          --no-color    disable terminal color
          --fresh       bypass an eligible read cache
          --confirm     approve a removal; never means approve an order
      HELP
      code
    end

    # Help is stdout too, so in JSON mode it has to be a document like
    # everything else -- otherwise `walmart --json` with no arguments emits
    # prose into a parser.
    def print_help_json(code)
      context(@options).formatter.emit_json(
        "name" => "walmart-cli",
        "version" => VERSION,
        "checks_out" => false,
        "commands" => CLI::COMMANDS.map { |path, (_h, summary)| command_doc(path, summary) },
        "planned" => CLI::PLANNED.map { |path, summary| command_doc(path, summary) }
      )
      code
    end

    def command_doc(path, summary)
      { "command" => path.join(" "), "summary" => summary }
    end

    # command_lines takes paths (arrays); these are already strings, and
    # reusing it turned `order sync --help` into an internal error.
    def flag_lines(pairs)
      width = pairs.map { |flag, _| flag.length }.max.to_i
      pairs.map { |flag, description| "  #{flag.ljust(width)}  #{description}" }.join("\n")
    end

    def command_lines(pairs)
      width = pairs.map { |path, _| path.join(" ").length }.max.to_i
      pairs.map { |path, summary| "  #{path.join(" ").ljust(width)}  #{summary}" }.join("\n")
    end
  end
end
