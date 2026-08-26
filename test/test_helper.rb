# frozen_string_literal: true

require "minitest/autorun"
require "fileutils"
require "json"
require "tmpdir"

$LOAD_PATH.unshift(File.expand_path("../lib", __dir__))
require "walmart"
require "walmart/commands/base"
require "walmart/commands/doctor"
require "walmart/commands/config_cmd"
require "walmart/commands/login"
require "walmart/commands/cart"
require "walmart/commands/search"

module Walmart
  # Base class for every test: hermetic XDG directories, no Keychain, no
  # browser, no network. A test that wants any of those must ask explicitly.
  class TestCase < Minitest::Test
    ROOT = File.expand_path("..", __dir__)

    def setup
      @tmp = Dir.mktmpdir("walmart-test")
      @env = {
        "HOME" => File.join(@tmp, "home"),
        "XDG_CONFIG_HOME" => File.join(@tmp, "config"),
        "XDG_DATA_HOME" => File.join(@tmp, "data"),
        "XDG_CACHE_HOME" => File.join(@tmp, "cache"),
        "XDG_STATE_HOME" => File.join(@tmp, "state"),
        "PATH" => ENV.fetch("PATH", "")
      }
      FileUtils.mkdir_p(@env["HOME"])
    end

    def teardown
      FileUtils.remove_entry(@tmp) if @tmp && File.directory?(@tmp)
    end

    attr_reader :tmp, :env

    def paths = @paths ||= Paths.new(env: env)

    def config(data = {})
      @config ||= Config.new(paths: paths, data: data)
    end

    def out = @out ||= StringIO.new
    def err = @err ||= StringIO.new

    def formatter(json: false, quiet: false)
      Formatter.new(json: json, quiet: quiet, color: "never", out: out, err: err)
    end

    def build_context(options: {}, worker: nil, secrets: nil, stdin: StringIO.new, json: false)
      options = { json: json, quiet: false, verbose: false, no_color: true,
                  fresh: false, confirm: false }.merge(options)
      Context.new(
        options: options,
        paths: paths,
        config: config,
        formatter: formatter(json: options[:json], quiet: options[:quiet]),
        secrets: secrets || Secrets.new(provider: Secrets::NullProvider.new),
        worker: worker,
        env: env,
        stdin: stdin
      )
    end

    # A Secrets provider that answers from a hash, so no test ever shells out
    # to `security` or prompts for biometrics.
    class FakeProvider
      def initialize(values = {}, available: true, error_accounts: [])
        @values = values
        @available = available
        @error_accounts = error_accounts.map(&:to_s)
      end

      def name = "fake"
      def available? = @available

      def probe(account)
        return :absent unless @available
        return :error if @error_accounts.include?(account.to_s)

        @values.key?(account.to_s) ? :present : :absent
      end

      def present?(account) = probe(account) == :present
      def read(account) = @values[account.to_s] ? Secrets::Secret.new(@values[account.to_s]) : nil
      def describe = "fake provider"
      def last_error = @error_accounts.empty? ? nil : "fake refusal"
    end

    # Records worker calls and replays scripted outcomes.
    class FakeWorker
      def initialize(results: {}, events: {}, errors: {})
        @results = results
        @events = events
        @errors = errors
        @calls = []
      end

      attr_reader :calls

      def run(action, params: {}, secrets: nil, options: {}, mutation: false, timeout: nil)
        @calls << { action: action, params: params, secrets: secrets, options: options,
                    mutation: mutation, timeout: timeout }
        Array(@events[action]).each { |event| yield event if block_given? }
        raise @errors[action] if @errors.key?(action)

        @results.fetch(action) { {} }
      end

      def python_command = ["python3"]
    end

    def stdout_json
      JSON.parse(out.string)
    end
  end
end
