# frozen_string_literal: true

require "open3"
require_relative "errors"
require_relative "which"

module Walmart
  # The credential-provider boundary.
  #
  # Rules from DESIGN.md that this file enforces:
  #   * secrets never appear in argv (we pass account names, never values),
  #   * secrets never reach a log, a config file, or an exception message,
  #   * a missing credential is not an error when manual login is possible,
  #   * there is no plaintext-config fallback, ever.
  #
  # Adding a Linux Secret Service provider means adding a class here and a
  # branch in .default. No command and no worker code changes.
  class Secrets
    SERVICE = "walmart-cli"
    EMAIL_ACCOUNT = "email"
    PASSWORD_ACCOUNT = "password"

    # A credential value that refuses to leak through inspection, interpolation
    # in a log line, or an exception dump. Call #value only where the secret is
    # actually needed (the worker's stdin).
    #
    # Every way Ruby can turn an object into text is covered here, because the
    # leak we are guarding against is never a deliberate print -- it is a
    # backtrace, a `p` left in during debugging, or an object that merely
    # happens to hold one getting logged. Serialization raises instead of
    # redacting: a Secret that round-trips as "[REDACTED]" would be a
    # credential that silently fails to log in, which is worse than a stack
    # trace at the line that tried.
    class Secret
      def initialize(value)
        @value = value.to_s
      end

      attr_reader :value

      def empty? = @value.empty?
      def to_s = "[REDACTED]"
      def inspect = "#<Walmart::Secrets::Secret [REDACTED]>"
      def to_json(*) = '"[REDACTED]"'
      def pretty_print(pp) = pp.text(inspect)

      # Marshal and Psych both read instance variables directly, so neither is
      # covered by any of the above.
      def marshal_dump = raise(Error.new(NOT_SERIALIZABLE, code: "secret_serialization"))
      def encode_with(_coder) = raise(Error.new(NOT_SERIALIZABLE, code: "secret_serialization"))

      NOT_SERIALIZABLE = "refusing to serialize a credential"
    end

    # Provider contract:
    #   #available?            -> can this provider be used at all
    #   #probe(account)        -> :present | :absent | :error
    #   #present?(account)     -> probe == :present
    #   #read(account)         -> Secret | nil
    class NullProvider
      def name = "none"
      def available? = false
      def probe(_account) = :absent
      def present?(_account) = false
      def read(_account) = nil
      def empty_accounts = []
      def describe = "no credential store configured (manual login only)"
      def last_error = nil
    end

    # macOS generic passwords, read with argv-array subprocess calls. No shell,
    # so no quoting bug can turn an account name into a command.
    #
    # Platform and PATH are injected so the behavior is testable on Linux CI
    # without pretending the machine is a Mac.
    class KeychainProvider
      # `security` exit codes we care about. 44 is "item not found", which is a
      # normal, expected answer. Anything else (a denied prompt, a locked
      # keychain) is an error, and conflating the two would tell the user to add
      # a record they already have.
      ITEM_NOT_FOUND = 44

      def initialize(service: SERVICE, runner: nil, platform: RUBY_PLATFORM, env: ENV)
        @service = service
        @platform = platform
        @env = env
        @runner = runner || method(:run_security)
        @last_error = nil
        @empty_accounts = []
      end

      attr_reader :last_error, :empty_accounts

      def name = "keychain"

      def available?
        return @available unless @available.nil?

        @available = @platform.include?("darwin") && !Which.find("security", env: @env).nil?
      end

      # Presence check that never asks for the secret itself: omitting -w makes
      # `security` print attributes only, so this does not unlock the value and
      # usually does not prompt for biometrics.
      def probe(account)
        return :absent unless available?

        status, = @runner.call(["find-generic-password", "-s", @service, "-a", account.to_s])
        case status
        when 0 then :present
        when ITEM_NOT_FOUND then :absent
        else
          @last_error = "security exited #{status} for account #{account}"
          :error
        end
      end

      def present?(account) = probe(account) == :present

      def read(account)
        return nil unless available?

        status, out = @runner.call(["find-generic-password", "-s", @service, "-a", account.to_s, "-w"])
        unless status.zero?
          @last_error = "security exited #{status} for account #{account}" unless status == ITEM_NOT_FOUND
          return nil
        end

        value = out.to_s.sub(/\n\z/, "")
        # An item that exists and holds nothing is its own state. `security`
        # exits 0 for it, and a presence probe -- which is all doctor may run,
        # since reading a value can prompt for biometrics -- cannot tell it from
        # a usable credential. Recording it here is what lets login say "the
        # password item is empty" instead of "no stored credentials found",
        # which sends someone to add a record they already have.
        if value.empty?
          @empty_accounts |= [account.to_s]
          return nil
        end

        Secret.new(value)
      end

      def describe = "macOS Keychain (service #{@service})"

      private

      def run_security(args)
        out, _err, status = Open3.capture3("security", *args)
        [status.exitstatus, out]
      rescue Errno::ENOENT
        [127, ""]
      end
    end

    # Placeholder so the shape of a future Linux provider is already decided.
    # Deliberately unavailable rather than half-implemented.
    class SecretServiceProvider
      def name = "secret-service"
      def available? = false
      def probe(_account) = :absent
      def present?(_account) = false
      def read(_account) = nil
      def empty_accounts = []
      def describe = "libsecret provider not implemented yet (manual login on Linux)"
      def last_error = nil
    end

    def self.default(platform: RUBY_PLATFORM)
      if platform.include?("darwin")
        new(provider: KeychainProvider.new)
      elsif platform.include?("linux")
        new(provider: SecretServiceProvider.new)
      else
        new(provider: NullProvider.new)
      end
    end

    def initialize(provider: NullProvider.new)
      @provider = provider
    end

    attr_reader :provider

    def provider_name = @provider.name
    def describe = @provider.describe
    def available? = @provider.available?

    def email_present? = probe(EMAIL_ACCOUNT) == :present
    def password_present? = probe(PASSWORD_ACCOUNT) == :present

    # :present, :absent, or :error. The distinction matters: "absent" means add
    # a record, "error" means the keychain refused us and adding another record
    # will not help.
    def probe(account)
      return :absent unless @provider.available?

      @provider.probe(account)
    end

    def unreadable? = [probe(EMAIL_ACCOUNT), probe(PASSWORD_ACCOUNT)].include?(:error)

    def last_error = @provider.respond_to?(:last_error) ? @provider.last_error : nil

    # True when an unattended-ish credential fill is possible. Never implies the
    # login will succeed; MFA and challenges are decided live.
    def complete? = email_present? && password_present?

    # Accounts whose stored value is empty. Only populated by a read, so this
    # is meaningful after credentials_payload has been asked for.
    def empty_accounts = @provider.empty_accounts

    def email = @provider.read(EMAIL_ACCOUNT)
    def password = @provider.read(PASSWORD_ACCOUNT)

    # The only method allowed to produce raw secret strings, and the only
    # consumer is Worker, which writes them to the worker's stdin. Returns nil
    # when credentials are unavailable, which callers must treat as "use a
    # manual login", not as an error.
    def credentials_payload
      mail = email
      pass = password
      return nil if mail.nil? || pass.nil? || mail.empty? || pass.empty?

      { "email" => mail.value, "password" => pass.value }
    end

    # Safe to print. Booleans and states only.
    def status
      {
        "provider" => provider_name,
        "available" => available?,
        "email" => probe(EMAIL_ACCOUNT).to_s,
        "password" => probe(PASSWORD_ACCOUNT).to_s,
        "email_present" => email_present?,
        "password_present" => password_present?
      }
    end

    def inspect = "#<Walmart::Secrets provider=#{provider_name}>"
    def to_s = inspect
  end
end
