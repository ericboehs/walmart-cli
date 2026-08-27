# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  class ConfigTest < TestCase
    def test_defaults_apply_without_a_file
      cfg = Config.new(paths: paths)

      assert_equal true, cfg["headless"]
      assert_equal 300, cfg["cache_ttl_seconds"]
      assert_nil cfg.store
      refute_predicate cfg, :store_pinned?
    end

    def test_save_and_reload_round_trips
      cfg = Config.new(paths: paths)
      cfg.set("cache_ttl_seconds", 60)
      cfg.store = { "store_id" => "100", "name" => "Test Store", "zip" => "00000" }
      cfg.save

      reloaded = Config.load(paths: paths)

      assert_equal 60, reloaded["cache_ttl_seconds"]
      assert_equal "100", reloaded.store["store_id"]
      assert_predicate reloaded, :store_pinned?
    end

    def test_config_file_is_owner_only
      Config.new(paths: paths).save

      assert_equal "600", format("%o", File.stat(paths.config_file).mode & 0o777)
    end

    # Safety invariant 8: config is never a credential store.
    def test_save_refuses_secret_looking_keys
      cfg = Config.new(paths: paths)
      cfg.data["password"] = "hunter2"

      error = assert_raises(ConfigError) { cfg.save }

      assert_match(/secret-looking config key: password/, error.message)
      refute_path_exists paths.config_file
    end

    def test_save_refuses_nested_secret_keys
      cfg = Config.new(paths: paths)
      cfg.data["store"] = { "store_id" => "1", "session_token" => "abc" }

      assert_raises(ConfigError) { cfg.save }
    end

    # DESIGN.md: pinning must not persist a street or delivery address.
    def test_store_assignment_drops_everything_but_id_name_zip
      cfg = Config.new(paths: paths)
      cfg.store = {
        "store_id" => "100", "name" => "Test", "zip" => "00000",
        "address" => "123 Country Road", "phone" => "555-555-5555"
      }

      assert_equal %w[store_id name zip].sort, cfg.store.keys.sort
      refute_includes cfg.to_h["store"].keys, "address"
    end

    # Reading is a validation boundary. A hand-edited file must fail on load,
    # not three commands later when "false" reads as truthy and the browser
    # stays invisible.
    def test_load_rejects_a_hand_edited_boolean
      write_config({ "headless" => "false" })

      error = assert_raises(ConfigError) { Config.load(paths: paths) }

      assert_match(/headless must be true or false/, error.message)
    end

    def test_load_rejects_a_negative_timeout
      write_config({ "worker_timeout_seconds" => -1 })

      assert_raises(ConfigError) { Config.load(paths: paths) }
    end

    def test_load_rejects_an_address_smuggled_into_store
      write_config({ "store" => { "store_id" => "1", "address" => "1234 Country Road" } })

      assert_raises(ConfigError) { Config.load(paths: paths) }
    end

    def test_load_accepts_a_valid_file
      write_config({ "headless" => false, "color" => "never" })

      assert_equal false, Config.load(paths: paths)["headless"]
    end

    def test_store_assignment_validates_immediately
      cfg = Config.new(paths: paths)

      assert_raises(ConfigError) { cfg.store = "not a hash" }
    end

    def test_store_assignment_still_drops_unpermitted_keys
      cfg = Config.new(paths: paths)
      cfg.store = { "store_id" => "5", "name" => "Tahlequah", "zip" => "74464",
                    "address" => "1234 Country Road" }

      assert_equal({ "store_id" => "5", "name" => "Tahlequah", "zip" => "74464" }, cfg.store)
      refute_includes cfg.to_h["store"].keys, "address"
    end

    def test_unknown_setting_is_rejected
      assert_raises(ConfigError) { Config.new(paths: paths).set("nope", 1) }
    end

    # A secret one array deep is still a secret in a plaintext file.
    def test_save_refuses_secret_keys_hidden_inside_arrays
      cfg = Config.new(paths: paths)
      cfg.data["store"] = { "store_id" => "1" }
      cfg.data["headless"] = true
      cfg.data["color"] = "auto"
      cfg.data["order_sync_years"] = 2
      cfg.data["extras"] = [{ "harmless" => 1 }, { "nested" => [{ "auth_token" => "abc" }] }]

      error = assert_raises(ConfigError) { cfg.save }

      assert_match(/secret-looking config key/, error.message)
      assert_match(/extras/, error.message)
      refute_path_exists paths.config_file
    end

    def test_validation_rejects_a_non_boolean_headless
      cfg = Config.new(paths: paths)
      cfg.data["headless"] = "false"

      error = assert_raises(ConfigError) { cfg.save }

      assert_match(/headless must be true or false/, error.message)
    end

    def test_validation_rejects_a_negative_cache_ttl
      assert_raises(ConfigError) { Config.new(paths: paths).set("cache_ttl_seconds", -1) }
    end

    def test_validation_allows_a_zero_cache_ttl
      assert_equal 0, Config.new(paths: paths).set("cache_ttl_seconds", 0)["cache_ttl_seconds"]
    end

    def test_validation_rejects_a_zero_timeout
      assert_raises(ConfigError) { Config.new(paths: paths).set("worker_timeout_seconds", 0) }
    end

    def test_validation_rejects_an_unknown_color_mode
      error = assert_raises(ConfigError) { Config.new(paths: paths).set("color", "rainbow") }

      assert_match(/color must be one of/, error.message)
    end

    def test_validation_rejects_an_unknown_browser_channel
      assert_raises(ConfigError) { Config.new(paths: paths).set("browser_channel", "safari") }
    end

    def test_validation_rejects_a_store_with_extra_keys
      cfg = Config.new(paths: paths)
      cfg.data["store"] = { "store_id" => "1", "address" => "1234 Country Road" }

      error = assert_raises(ConfigError) { cfg.save }

      assert_match(/store may only contain/, error.message)
    end

    def test_validation_rejects_a_non_object_store
      cfg = Config.new(paths: paths)
      cfg.data["store"] = "my store"

      assert_raises(ConfigError) { cfg.save }
    end

    def test_malformed_json_explains_itself
      FileUtils.mkdir_p(File.dirname(paths.config_file))
      File.write(paths.config_file, "{not json")

      error = assert_raises(ConfigError) { Config.load(paths: paths) }

      assert_match(/not valid JSON/, error.message)
      assert_match(/config\.json/, error.message)
    end

    private

    def write_config(data)
      FileUtils.mkdir_p(File.dirname(paths.config_file))
      File.write(paths.config_file, JSON.pretty_generate(data))
    end

    # cdp_url points at an unauthenticated debugging port. Anything that can
    # reach it can drive the browser and read every cookie in the profile, so
    # the only acceptable hosts are loopback ones -- and the host is parsed,
    # never substring-matched, because "127.0.0.1.evil.com" contains "127.0.0.1".
    def test_a_cdp_url_may_only_point_at_loopback
      %w[http://127.0.0.1:9222 http://localhost:9222 http://[::1]:9222].each do |url|
        assert_equal url, Config.new(paths: paths, data: { "cdp_url" => url }).validate!.fetch("cdp_url")
      end
    end

    def test_a_cdp_url_on_another_host_is_refused_with_the_reason
      error = assert_raises(ConfigError) do
        Config.new(paths: paths, data: { "cdp_url" => "http://192.168.1.50:9222" }).validate!
      end

      assert_match(/loopback/, error.message)
      assert_match(/unauthenticated/, error.message)
    end

    def test_a_lookalike_loopback_host_is_refused
      ["http://127.0.0.1.evil.com:9222", "http://localhost.evil.com:9222",
       "http://evil.com/127.0.0.1"].each do |url|
        assert_raises(ConfigError, "#{url} must not pass as loopback") do
          Config.new(paths: paths, data: { "cdp_url" => url }).validate!
        end
      end
    end

    def test_a_cdp_url_that_is_not_http_is_refused
      ["ftp://127.0.0.1", "file:///etc/passwd", "not a url at all", "127.0.0.1:9222"].each do |url|
        assert_raises(ConfigError, "#{url} must not be accepted") do
          Config.new(paths: paths, data: { "cdp_url" => url }).validate!
        end
      end
    end

    def test_an_absent_cdp_url_is_the_ordinary_case
      assert_nil Config.new(paths: paths, data: {}).validate!.fetch("cdp_url")
      assert_nil Config.new(paths: paths, data: { "cdp_url" => nil }).validate!.fetch("cdp_url")
    end

    # A pinned store this cannot describe is worse than no store: it passes
    # store_pinned? and is then compared against a real store id. Every one of
    # these validated happily before `store pin` existed to write one.
    def test_a_store_without_a_store_id_is_refused
      assert_raises(ConfigError) { config.store = { "name" => "Owasso" } }
    end

    def test_an_empty_store_object_is_refused
      assert_raises(ConfigError) { config.store = {} }
    end

    [["an integer", 499], ["true", true], ["an array", %w[499]],
     ["a hash", { "id" => "1" }], ["blank", "  "], ["a letter", "4A9"]].each do |name, value|
      define_method("test_a_store_id_that_is_#{name.tr(" ", "_")}_is_refused") do
        assert_raises(ConfigError) { config.store = { "store_id" => value } }
      end
    end

    def test_a_digit_string_store_id_is_accepted
      config.store = { "store_id" => "0499" }

      assert_equal "0499", config.store["store_id"]
    end

    # Still permitted, because a config written by hand before `store pin`
    # existed may hold them. Nothing writes them.
    def test_a_name_and_zip_are_allowed_alongside_a_valid_store_id
      config.store = { "store_id" => "499", "name" => "Owasso", "zip" => "74055" }

      assert_equal "Owasso", config.store["name"]
    end

    def test_a_blank_name_is_refused_rather_than_stored_as_an_answer
      assert_raises(ConfigError) { config.store = { "store_id" => "499", "name" => " " } }
    end
  end
end
