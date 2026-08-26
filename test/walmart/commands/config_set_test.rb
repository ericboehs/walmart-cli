# frozen_string_literal: true

require "test_helper"

module Walmart
  module Commands
    # `walmart config set|unset`.
    #
    # A command line has no types. Everything arrives as a string, and the
    # config file is validated on the assumption that it does not -- writing
    # "false" as a string is exactly the failure Config#validate! exists to
    # catch, so the coercion here answers to it rather than guessing.
    class ConfigSetTest < TestCase
      def test_a_boolean_is_written_as_a_boolean_not_a_string
        run_config(%w[config set headless false])

        assert_equal false, reloaded.fetch("headless")
      end

      def test_the_words_people_actually_type_for_true_and_false_are_accepted
        { "true" => true, "yes" => true, "on" => true, "1" => true,
          "false" => false, "no" => false, "off" => false, "0" => false }.each do |word, expected|
          run_config(["config", "set", "headless", word])

          assert_equal expected, reloaded.fetch("headless"), "headless=#{word}"
        end
      end

      def test_a_word_that_is_neither_true_nor_false_is_refused
        error = assert_raises(UsageError) { run_config(%w[config set headless maybe]) }

        assert_match(/true or false/, error.message)
        refute_path_exists paths.config_file, "nothing should be written when the value is rejected"
      end

      def test_an_integer_setting_rejects_words
        error = assert_raises(UsageError) { run_config(%w[config set cache_ttl_seconds soon]) }

        assert_match(/whole number/, error.message)
      end

      def test_an_integer_setting_is_written_as_a_number
        run_config(%w[config set cache_ttl_seconds 60])

        assert_equal 60, reloaded.fetch("cache_ttl_seconds")
      end

      def test_an_unknown_setting_is_refused_rather_than_invented
        assert_raises(ConfigError) { run_config(%w[config set colour never]) }
      end

      # The setting this whole feature exists for.
      def test_a_cdp_url_round_trips
        run_config(%w[config set cdp_url http://127.0.0.1:9222])

        assert_equal "http://127.0.0.1:9222", reloaded.fetch("cdp_url")
      end

      # Validation runs before the write, so a rejected value cannot land on
      # disk and be loaded by the next command.
      def test_a_rejected_cdp_url_never_reaches_the_file
        assert_raises(ConfigError) { run_config(%w[config set cdp_url http://192.168.1.9:9222]) }

        refute_path_exists paths.config_file
      end

      def test_unset_restores_the_default
        run_config(%w[config set cdp_url http://127.0.0.1:9222])
        run_config(%w[config unset cdp_url])

        assert_nil reloaded.fetch("cdp_url")
      end

      def test_the_store_is_not_settable_as_a_string
        error = assert_raises(UsageError) { run_config(%w[config set store 1234]) }

        assert_match(/own command/, error.message)
      end

      def test_set_without_a_value_is_a_usage_error
        assert_raises(UsageError) { run_config(%w[config set headless]) }
      end

      def test_unset_without_a_setting_is_a_usage_error
        assert_raises(UsageError) { run_config(%w[config unset]) }
      end

      def test_json_reports_the_value_that_was_written
        run_config(%w[config set cache_ttl_seconds 30], json: true)
        payload = JSON.parse(out.string)

        assert_equal "cache_ttl_seconds", payload["setting"]
        assert_equal 30, payload["value"]
      end

      private

      def run_config(argv, json: false)
        command = ConfigCmd.new(build_context(json: json))
        path = argv.take(2)
        command.run(path, argv.drop(2))
      end

      def reloaded
        Config.load(paths: paths)
      end
    end
  end
end
