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
    private

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

    def command_lines(pairs)
      width = pairs.map { |path, _| path.join(" ").length }.max.to_i
      pairs.map { |path, summary| "  #{path.join(" ").ljust(width)}  #{summary}" }.join("\n")
    end
  end
end
