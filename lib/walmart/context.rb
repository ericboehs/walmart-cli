# frozen_string_literal: true

require_relative "config"
require_relative "formatter"
require_relative "paths"
require_relative "secrets"
require_relative "worker"

module Walmart
  # Everything a command needs, injectable so tests never touch the real
  # environment, the real Keychain, or the real browser profile.
  class Context
    def initialize(options:, paths: nil, config: nil, formatter: nil, secrets: nil,
                   worker: nil, env: ENV, stdin: $stdin, stderr: $stderr)
      @options = options
      @env = env
      @stdin = stdin
      # Only for warnings raised while deciding how to build the formatter
      # itself; everything else goes through formatter.err.
      @stderr = stderr
      @paths = paths
      @config = config
      @formatter = formatter
      @secrets = secrets
      @worker = worker
    end

    attr_reader :options, :env, :stdin

    def paths = @paths ||= Paths.new(env: @env)
    def config = @config ||= Config.load(paths: paths)
    def secrets = @secrets ||= Secrets.default

    def formatter
      @formatter ||= Formatter.new(
        json: json?, quiet: quiet?, color: color_mode
      )
    end

    def worker
      @worker ||= Worker.new(paths: paths, config: config, env: @env, logger: method(:log))
    end

    def json?    = !!@options[:json]
    def quiet?   = !!@options[:quiet]
    def verbose? = !!@options[:verbose]
    def fresh?   = !!@options[:fresh]
    def confirm? = !!@options[:confirm]

    # An unreadable or invalid config must not stop us from choosing a color
    # mode, but it must not pass unmentioned either: the user edited the file
    # and deserves to know it was ignored. Every other command path raises the
    # same ConfigError loudly, so this is a one-line notice, not a swallow.
    def color_mode
      return "never" if @options[:no_color]

      config.fetch("color", "auto")
    rescue ConfigError => e
      unless @color_mode_warned
        @color_mode_warned = true
        @stderr.puts("warning: #{e.message}; using color mode 'auto'")
      end
      "auto"
    end

    # True when we may prompt: a TTY on both ends and not a machine-readable run.
    # Safety invariant 7: --json never prompts.
    def interactive?
      return false if json?

      @stdin.respond_to?(:tty?) && @stdin.tty? && formatter.out.respond_to?(:tty?) && formatter.out.tty?
    end

    def log(level, message)
      return unless verbose?

      formatter.err.puts("[#{level}] #{message}")
    end
  end
end
