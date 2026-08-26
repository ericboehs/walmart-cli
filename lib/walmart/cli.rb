# frozen_string_literal: true

require_relative "context"
require_relative "errors"
require_relative "redact"
require_relative "version"
require_relative "commands/doctor"
require_relative "commands/config_cmd"
require_relative "commands/cart"
require_relative "commands/item"
require_relative "commands/login"
require_relative "commands/order"
require_relative "commands/search"

module Walmart
  # Argument parsing and dispatch.
  #
  # The command table below is the CLI's entire public surface. Safety
  # invariant 1 is partly enforced here: no entry names a forbidden operation
  # (see Walmart::Safety), and the surface-scan suite
  # (test/walmart/*_surface_test.rb) fails the build if one is ever added.
  class CLI
    GLOBAL_FLAGS = {
      "--json" => :json,
      "--quiet" => :quiet,
      "-q" => :quiet,
      "--verbose" => :verbose,
      "-v" => :verbose,
      "--no-color" => :no_color,
      "--fresh" => :fresh,
      "--confirm" => :confirm
    }.freeze

    # command path => [handler, summary]
    COMMANDS = {
      %w[version] => [nil, "print the walmart-cli version"],
      %w[doctor] => [Commands::Doctor, "check the local environment and session"],
      %w[login] => [Commands::Login, "sign in with a visible browser"],
      %w[logout] => [Commands::Login, "forget the stored browser session"],
      %w[config show] => [Commands::ConfigCmd, "print current settings"],
      %w[config path] => [Commands::ConfigCmd, "print the config file path"],
      %w[config init] => [Commands::ConfigCmd, "write a default config file"],
      %w[search] => [Commands::Search, "search Walmart's catalog"],
      %w[cart show] => [Commands::Cart, "read the live shared cart"],
      %w[order sync] => [Commands::Order, "archive orders and receipts"],
      %w[item] => [Commands::Item, "show one item by id or URL"]
    }.freeze

    # Declared now so `--help` tells the truth about where the project is,
    # rather than pretending unimplemented commands work.
    PLANNED = {
      %w[store show] => "compare the pinned store with Walmart's active store",
      %w[store pin] => "pin the active grocery store",
      %w[cart add] => "add an exact item to the cart",
      %w[cart increment] => "increase a cart line's quantity",
      %w[cart decrement] => "decrease a cart line's quantity",
      %w[cart remove] => "remove a cart line",
      %w[cart add-from-order] => "reorder items from an archived order",
      %w[my-items list] => "browse Walmart's My Items",
      %w[my-items search] => "search Walmart's My Items",
      %w[order list] => "list archived orders",
      %w[order show] => "show one archived order",
      %w[order search] => "search the archive"
    }.freeze

    def initialize(argv, env: ENV, stdout: $stdout, stderr: $stderr, stdin: $stdin, context: nil)
      @argv = argv.dup
      @env = env
      @stdout = stdout
      @stderr = stderr
      @stdin = stdin
      @injected_context = context
    end

    def self.run(argv, **kwargs)
      new(argv, **kwargs).run
    end

    def run
      options, words = parse_globals(@argv)

      return print_help(ExitCodes::OK) if words.empty? || help?(words)
      return print_version(options) if words.first == "version"

      path, rest = match_command(words)
      return unknown_command(words) unless path

      dispatch(path, rest, options)
    rescue Error => e
      # UsageError, SafetyError, WorkerError, and UnverifiedError all carry the
      # exit code that describes what actually happened.
      report(e, verbose: options&.fetch(:verbose, false))
      e.exit_code
    rescue Interrupt
      # Exit 2 (USAGE) is reused deliberately: the documented contract has four
      # codes, and "the run did not happen" is the closest of the four. Shell
      # convention would be 130; adopting it would be a contract change.
      @stderr.puts("\naborted")
      ExitCodes::USAGE
    rescue StandardError => e
      # A bug, not a known failure mode. It still has to leave through the
      # documented exit codes rather than as a raw Ruby backtrace.
      internal_error(e, verbose: options&.fetch(:verbose, false))
    end

    private

    def parse_globals(argv)
      options = { json: false, quiet: false, verbose: false, no_color: false,
                  fresh: false, confirm: false }
      words = []
      argv.each do |arg|
        if GLOBAL_FLAGS.key?(arg)
          options[GLOBAL_FLAGS[arg]] = true
        else
          words << arg
        end
      end
      [options, words]
    end

    def help?(words)
      words.first == "help" || words.include?("--help") || words.include?("-h")
    end

    def match_command(words)
      # Longest declared prefix wins, so `config show` beats a bare `config`.
      COMMANDS.keys.sort_by { |path| -path.length }.each do |path|
        return [path, words[path.length..]] if words.first(path.length) == path
      end
      nil
    end

    def dispatch(path, rest, options)
      handler = COMMANDS.fetch(path).first
      context = build_context(options)
      handler.new(context).run(path, rest)
    end

    def build_context(options)
      return @injected_context if @injected_context

      Context.new(
        options: options,
        env: @env,
        stdin: @stdin,
        formatter: Formatter.new(
          json: options[:json], quiet: options[:quiet],
          color: options[:no_color] ? "never" : "auto",
          out: @stdout, err: @stderr
        )
      )
    end

    def print_version(options)
      if options[:json]
        @stdout.puts(JSON.pretty_generate({ "name" => "walmart-cli", "version" => VERSION }))
      else
        @stdout.puts("walmart-cli #{VERSION}")
      end
      ExitCodes::OK
    end

    def unknown_command(words)
      @stderr.puts("error: unknown command: #{words.join(" ")}")
      @stderr.puts("run `walmart help` for the command list")
      ExitCodes::USAGE
    end

    def report(error, verbose: false)
      @stderr.puts("error: #{error.message}")
      @stderr.puts("run: walmart login") if error.is_a?(WorkerError) && error.login_required?
      report_details(error) if verbose
    end

    # The full stderr tail and exit status live in details. They are already
    # redacted by Worker; --verbose is what decides whether the user wants them.
    def report_details(error)
      details = error.details
      return if !details.is_a?(Hash) || details.empty?

      @stderr.puts("details:")
      details.each do |key, value|
        Array(value).each { |line| @stderr.puts("  #{key}: #{line}") } if value.is_a?(Array)
        @stderr.puts("  #{key}: #{value}") unless value.is_a?(Array)
      end
    end

    def internal_error(error, verbose: false)
      @stderr.puts("internal error: #{error.class}: #{Redact.text(error.message)}")
      if verbose
        Redact.tail(error.backtrace, limit: 20).each { |line| @stderr.puts("  #{line}") }
      else
        @stderr.puts("run again with --verbose for a backtrace")
      end
      ExitCodes::FAILED
    end

    def print_help(code)
      @stdout.puts(<<~HELP)
        walmart #{VERSION} - build a Walmart grocery delivery cart from the command line

        This tool never checks out. Review substitutions, the delivery slot, tip,
        payment, and the final order in Walmart's app.

        Available now:
        #{command_lines(COMMANDS.map { |path, (_h, summary)| [path, summary] })}

        Planned (not yet implemented):
        #{command_lines(PLANNED.to_a)}

        Global flags:
          --json        one complete machine-readable JSON document
          -q, --quiet   suppress non-essential status output
          -v, --verbose diagnostic logs (never secrets)
          --no-color    disable terminal color
          --fresh       bypass an eligible read cache
          --confirm     approve a removal; never means approve an order
      HELP
      code
    end

    def command_lines(pairs)
      width = pairs.map { |path, _| path.join(" ").length }.max.to_i
      pairs.map { |path, summary| "  #{path.join(" ").ljust(width)}  #{summary}" }.join("\n")
    end
  end
end
