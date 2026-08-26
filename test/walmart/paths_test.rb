# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  class PathsTest < TestCase
    def test_every_path_lives_under_the_xdg_roots
      assert_equal File.join(tmp, "config/walmart/config.json"), paths.config_file
      assert_equal File.join(tmp, "data/walmart/browser-profile"), paths.browser_profile
      assert_equal File.join(tmp, "data/walmart/orders"), paths.orders_dir
      assert_equal File.join(tmp, "data/walmart/index.json"), paths.index_file
      assert_equal File.join(tmp, "cache/walmart/live"), paths.cache_dir
      assert_equal File.join(tmp, "state/walmart/sync.log"), paths.sync_log
    end

    def test_falls_back_to_home_when_xdg_is_unset
      bare = Paths.new(env: { "HOME" => "/home/someone" })

      assert_equal "/home/someone/.config/walmart/config.json", bare.config_file
      assert_equal "/home/someone/.local/share/walmart/browser-profile", bare.browser_profile
      assert_equal "/home/someone/.local/state/walmart/sync.log", bare.sync_log
    end

    def test_empty_xdg_values_are_treated_as_unset
      bare = Paths.new(env: { "HOME" => "/home/someone", "XDG_CONFIG_HOME" => "" })

      assert_equal "/home/someone/.config/walmart/config.json", bare.config_file
    end

    def test_private_directories_are_owner_only
      dir = paths.ensure_private_dir!(paths.browser_profile)

      assert_equal "700", format("%o", File.stat(dir).mode & 0o777)
    end

    def test_order_files_are_partitioned_by_year
      assert_equal File.join(tmp, "data/walmart/orders/2026/ABC-123.json"),
                   paths.order_file(2026, "ABC-123")
    end
  end
end
