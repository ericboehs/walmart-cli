# frozen_string_literal: true

require "json"
require "uri"
require "fileutils"
require_relative "errors"
require_relative "paths"

module Walmart
  # Non-secret settings, persisted as JSON under $XDG_CONFIG_HOME/walmart.
  #
  # Safety invariant 8: credentials never live here. #save refuses to write a
  # key whose name suggests a secret, so a future careless caller cannot turn
  # this file into a plaintext credential store.
  class Config
    SECRET_KEY_PATTERN = /pass|secret|token|cookie|session|credential|auth|otp|totp|apikey|api_key/i

    DEFAULTS = {
      # Pinned grocery store: {"store_id","name","zip"} and nothing else.
      "store" => nil,
      # Headless for normal commands; login is always visible.
      "headless" => true,
      # Preferred browser channel: "chrome", "chromium", or nil to autodetect.
      "browser_channel" => nil,
      # When set, attach to a Chrome the user is already running and signed into
      # rather than launching one (DESIGN.md: be a real browser, not a fake
      # human). Loopback only -- see assert_cdp_url.
      "cdp_url" => nil,
      # Short read cache for search/item/my-items. Cart reads are never cached.
      "cache_ttl_seconds" => 300,
      # Rolling window for `order sync` when neither --year nor --all is given.
      "order_sync_years" => 2,
      # Worker timeout for a single action.
      "worker_timeout_seconds" => 120,
      # "auto" | "always" | "never"
      "color" => "auto"
    }.freeze

    # Keys `store pin` is allowed to persist. Street and delivery addresses are
    # deliberately absent (DESIGN.md: sensitive archive data).
    STORE_KEYS = %w[store_id name zip].freeze

    # The only store field ever observed in a Walmart response is storeId, in
    # the cart's fulfillment node, and every one seen has been digits. A string
    # rather than an integer because leading zeros are Walmart's to keep, and
    # "0499".to_i would silently become a different store.
    #
    # name and zip remain permitted keys because an older config may hold them,
    # but nothing writes them: see "Store pinning" in DESIGN.md.
    STORE_ID = /\A\d+\z/

    COLOR_MODES = %w[auto always never].freeze
    BROWSER_CHANNELS = [nil, "chrome", "chromium", "msedge"].freeze
    # Hosts a CDP endpoint may live on. Parsed, never substring-matched.
    LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "::1", "[::1]"].freeze

    attr_reader :paths

    def initialize(paths: Paths.new, data: nil)
      @paths = paths
      @data = data ? DEFAULTS.merge(data) : nil
    end

    def self.load(paths: Paths.new)
      new(paths: paths).tap(&:data)
    end

    # Reading a config is a validation boundary: a hand-edited file with
    # "headless": "false" must fail here, loudly, rather than three commands
    # later when a string reads as truthy and the browser stays invisible.
    def data
      return @data if @data

      @data = DEFAULTS.merge(read_file)
      validate!
      @data
    end

    def [](key) = data[key.to_s]

    def fetch(key, default = nil)
      value = data[key.to_s]
      value.nil? ? default : value
    end

    def set(key, value)
      key = key.to_s
      raise ConfigError, "unknown setting: #{key}" unless DEFAULTS.key?(key)

      data[key] = value
      validate!
      self
    end

    # The pinned store, or nil. Always returns only the permitted keys.
    def store
      raw = data["store"]
      return nil unless raw.is_a?(Hash)

      raw.slice(*STORE_KEYS)
    end

    # Slices to the permitted keys on the way in, then validates: an address
    # must never reach the file, and a wrong shape must not wait until save.
    def store=(hash)
      raise ConfigError, "store must be an object containing store_id" unless hash.nil? || hash.is_a?(Hash)

      data["store"] = hash&.transform_keys(&:to_s)&.slice(*STORE_KEYS)
      validate!
      self
    end

    def store_pinned? = !store&.dig("store_id").to_s.empty?

    def save
      reject_secrets!(data)
      validate!
      dir = File.dirname(paths.config_file)
      paths.ensure_private_dir!(dir)
      tmp = "#{paths.config_file}.tmp"
      # Created private rather than tightened afterwards: write-then-chmod
      # leaves the file at the umask's mercy for the moment in between.
      #
      # TRUNC here, EXCL in the archive, on purpose. A config re-save is
      # routine, and refusing to write someone's settings because of debris
      # from an old crash is worse than overwriting the debris. An archive temp
      # file is either a crash worth noticing or a concurrent writer worth
      # refusing.
      body = "#{JSON.pretty_generate(data)}\n"
      File.open(tmp, File::WRONLY | File::CREAT | File::TRUNC, 0o600) { |file| file.write(body) }
      File.chmod(0o600, tmp)
      File.rename(tmp, paths.config_file)
      self
    end

    def exist? = File.exist?(paths.config_file)

    def to_h = data.dup

    # Type and domain checks for the settings we actually branch on. Called on
    # load, on every mutation, and again before writing, so an invalid value
    # has no path to disk and no path into a command.
    def validate!
      assert_boolean("headless")
      assert_non_negative_integer("cache_ttl_seconds")
      assert_positive_integer("order_sync_years")
      assert_positive_integer("worker_timeout_seconds")
      assert_member("color", COLOR_MODES)
      assert_member("browser_channel", BROWSER_CHANNELS)
      assert_cdp_url
      assert_store_shape
      self
    end

    private

    # A CDP endpoint is unauthenticated: anything that can reach it can drive
    # the browser and read its cookies. Refusing a non-loopback host here is
    # what stops a config edit from exposing a signed-in browser to the network.
    def assert_cdp_url
      value = data["cdp_url"]
      return if value.nil? || value == ""

      uri = begin
        URI.parse(value.to_s)
      rescue URI::InvalidURIError
        nil
      end
      unless uri && %w[http https].include?(uri.scheme) && uri.host
        raise ConfigError, "cdp_url must be an http(s) URL, got #{value.inspect}"
      end
      # Downcased because host names are case-insensitive: "LOCALHOST" is
      # loopback, and refusing it was a correctness bug rather than a safe
      # default. The worker's copy of this check parses the same way.
      return if LOOPBACK_HOSTS.include?(uri.host.to_s.downcase)

      raise ConfigError,
            "cdp_url must point at loopback (#{LOOPBACK_HOSTS.join(", ")}), got #{uri.host.inspect}: " \
            "a remote debugging port is unauthenticated, so exposing it off-host " \
            "hands the browser session to anyone who can reach it"
    end

    def assert_boolean(key)
      value = data[key]
      return if [true, false].include?(value)

      raise ConfigError, "#{key} must be true or false, got #{value.inspect}"
    end

    def assert_non_negative_integer(key)
      value = data[key]
      return if value.is_a?(Integer) && !value.negative?

      raise ConfigError, "#{key} must be a non-negative integer, got #{value.inspect}"
    end

    def assert_positive_integer(key)
      value = data[key]
      return if value.is_a?(Integer) && value.positive?

      raise ConfigError, "#{key} must be a positive integer, got #{value.inspect}"
    end

    def assert_member(key, allowed)
      value = data[key]
      return if allowed.include?(value)

      raise ConfigError, "#{key} must be one of #{allowed.map(&:inspect).join(", ")}, got #{value.inspect}"
    end

    # A pinned store is what the cart mutation compares itself against, so a
    # store this cannot describe is worse than no store at all: it would pass
    # `store_pinned?` and then be compared against a real store id.
    #
    # Every branch here has been reachable. `store = {}` validated happily,
    # store_id could be an Array, a Hash or true, and `to_s` downstream turned
    # any of them into something that looked like an answer.
    def assert_store_shape
      value = data["store"]
      return if value.nil?
      raise ConfigError, "store must be an object containing store_id" unless value.is_a?(Hash)

      extra = value.keys.map(&:to_s) - STORE_KEYS
      unless extra.empty?
        raise ConfigError, "store may only contain #{STORE_KEYS.join(", ")}; found #{extra.join(", ")}"
      end

      assert_store_id(value["store_id"])
      assert_optional_store_text(value)
    end

    def assert_store_id(id)
      # `is_a?(String)` first, because true and 499 both answer to to_s and
      # neither is a store.
      return if id.is_a?(String) && id.match?(STORE_ID)

      raise ConfigError, "store_id must be a string of digits; got #{id.inspect}"
    end

    def assert_optional_store_text(value)
      %w[name zip].each do |key|
        text = value[key]
        next if text.nil?
        next if text.is_a?(String) && !text.strip.empty?

        raise ConfigError, "store #{key} must be a non-empty string when present; got #{text.inspect}"
      end
    end

    def read_file
      return {} unless File.exist?(paths.config_file)

      raw = File.read(paths.config_file)
      return {} if raw.strip.empty?

      parsed = JSON.parse(raw)
      raise ConfigError, "config is not a JSON object: #{paths.config_file}" unless parsed.is_a?(Hash)

      parsed
    rescue JSON::ParserError => e
      raise ConfigError, "config is not valid JSON (#{paths.config_file}): #{e.message}"
    end

    # Walks hashes *and* arrays. A secret hidden one array deep is still a
    # secret in a plaintext file.
    def reject_secrets!(value, trail = [])
      case value
      when Hash
        value.each do |key, item|
          path = trail + [key.to_s]
          if key.to_s.match?(SECRET_KEY_PATTERN)
            raise ConfigError, "refusing to write secret-looking config key: #{path.join(".")}"
          end

          reject_secrets!(item, path)
        end
      when Array
        value.each_with_index { |item, index| reject_secrets!(item, trail + ["[#{index}]"]) }
      end
    end
  end
end
