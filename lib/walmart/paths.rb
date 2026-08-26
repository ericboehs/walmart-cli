# frozen_string_literal: true

require "fileutils"

module Walmart
  # XDG base-directory resolution for every file this CLI owns.
  #
  # Nothing else in the codebase may build a path out of ENV["HOME"]. Tests
  # point the four XDG variables at a scratch directory and get a hermetic
  # install for free.
  class Paths
    APP = "walmart"

    def initialize(env: ENV)
      @env = env
    end

    def config_home = base("XDG_CONFIG_HOME", ".config")
    def data_home   = base("XDG_DATA_HOME", ".local/share")
    def cache_home  = base("XDG_CACHE_HOME", ".cache")
    def state_home  = base("XDG_STATE_HOME", ".local/state")

    def config_file = File.join(config_home, "config.json")
    def browser_profile = File.join(data_home, "browser-profile")
    def profile_lock = File.join(data_home, "browser-profile.lock")
    def orders_dir   = File.join(data_home, "orders")
    def index_file   = File.join(data_home, "index.json")
    def cache_dir    = File.join(cache_home, "live")
    def sync_log     = File.join(state_home, "sync.log")

    def order_file(year, id)
      File.join(orders_dir, year.to_s, "#{id}.json")
    end

    # Creates a directory the current user alone can read.
    def ensure_private_dir!(path)
      FileUtils.mkdir_p(path)
      File.chmod(0o700, path)
      path
    end

    # Every directory the CLI may write to, in creation order.
    def all_dirs
      [config_home, data_home, cache_home, state_home]
    end

    def to_h
      {
        "config_file" => config_file,
        "browser_profile" => browser_profile,
        "orders_dir" => orders_dir,
        "index_file" => index_file,
        "cache_dir" => cache_dir,
        "sync_log" => sync_log
      }
    end

    private

    def base(var, fallback)
      root = @env[var]
      root = File.join(home, fallback) if root.nil? || root.empty?
      File.join(root, APP)
    end

    def home
      @env["HOME"] || Dir.home
    end
  end
end
