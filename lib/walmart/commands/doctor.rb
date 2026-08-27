# frozen_string_literal: true

require "json"
require_relative "base"
require_relative "attachment_check"
require_relative "../version"
require_relative "../which"

module Walmart
  module Commands
    # `walmart doctor` - explain the local install before anything touches the
    # network. Every check is local: the worker probe inspects the Python
    # environment and browser binaries, and never contacts Walmart.
    class Doctor < Base
      include AttachmentCheck

      # Chromium writes these into a persistent profile and removes them on a
      # clean exit. Named here rather than probed with ps, because the process
      # is not ours to identify and a name match would be a guess.
      SINGLETON_LOCKS = %w[SingletonLock SingletonSocket SingletonCookie].freeze

      Check = Struct.new(:name, :status, :detail, :hint, keyword_init: true) do
        def ok?   = status == :ok
        def fail? = status == :fail

        def to_h
          { "name" => name, "status" => status.to_s, "detail" => detail, "hint" => hint }.compact
        end
      end

      def run(_path, _args)
        checks = []
        checks << ruby_check
        checks << python_check
        checks.concat(worker_checks)
        checks.concat(path_checks)
        checks << config_check
        checks << credentials_check
        checks << store_check
        checks << picker_check
        checks << attachment_check
        checks << profile_lock_check

        render(checks)
        checks.any?(&:fail?) ? ExitCodes::FAILED : ExitCodes::OK
      end

      private

      def ruby_check
        Check.new(name: "ruby", status: :ok, detail: RUBY_DESCRIPTION.split.first(2).join(" "))
      end

      def python_check
        command = worker.python_command
        Check.new(name: "python", status: :ok, detail: command.join(" "))
      rescue Error => e
        Check.new(name: "python", status: :fail, detail: e.message,
                  hint: "install python3 (or uv) and re-run walmart doctor")
      end

      # Asks the worker to describe itself. A failure here is a real failure:
      # nothing else works without it.
      def worker_checks
        data = worker.run("probe", timeout: 60)
        [
          Check.new(name: "worker", status: :ok, detail: "protocol #{data["protocol"]}"),
          playwright_check(data),
          browser_check(data)
        ]
      rescue Error => e
        [Check.new(name: "worker", status: :fail, detail: e.message,
                   hint: "cd pyworker && uv sync")]
      end

      def playwright_check(data)
        if data["playwright"]
          Check.new(name: "playwright", status: :ok, detail: data["playwright"].to_s)
        else
          Check.new(name: "playwright", status: :fail, detail: "not importable",
                    hint: "cd pyworker && uv sync && uv run playwright install chromium")
        end
      end

      # "No browser installed" and "Playwright is broken" need opposite fixes,
      # so a lookup that failed for a reason says so instead of advising an
      # install that will not help.
      def browser_check(data)
        browsers = data["browsers"] || {}
        errors = data["browser_errors"] || {}
        found = browsers.select { |_name, path| path }
        if !found.empty?
          detail = found.keys.join(", ")
          detail += " (#{errors.values.join("; ")})" unless errors.empty?
          Check.new(name: "browser", status: errors.empty? ? :ok : :warn, detail: detail)
        elsif errors.empty?
          Check.new(name: "browser", status: :fail, detail: "no Chrome or Chromium found",
                    hint: "install Google Chrome, or: uv run playwright install chromium")
        else
          Check.new(name: "browser", status: :fail,
                    detail: "browser lookup failed: #{errors.values.join("; ")}",
                    hint: "a browser may be installed but unusable; " \
                          "try: cd pyworker && uv run playwright install chromium")
        end
      end

      def path_checks
        paths.all_dirs.map do |dir|
          writable = writable_dir?(dir)
          Check.new(
            name: "path #{File.basename(File.dirname(dir))}/#{File.basename(dir)}",
            status: writable ? :ok : :fail,
            detail: dir,
            hint: writable ? nil : "cannot create or write #{dir}"
          )
        end
      end

      def writable_dir?(dir)
        return File.writable?(dir) if File.directory?(dir)

        parent = File.dirname(dir)
        parent = File.dirname(parent) until File.directory?(parent) || parent == "/"
        File.writable?(parent)
      end

      def config_check
        config.to_h
        Check.new(name: "config", status: :ok,
                  detail: config.exist? ? paths.config_file : "defaults (no file yet)")
      rescue ConfigError => e
        Check.new(name: "config", status: :fail, detail: e.message,
                  hint: "fix or delete #{paths.config_file}")
      end

      # Booleans and states only. This never reads, prints, or logs a value.
      def credentials_check
        status = secrets.status
        if status["email"] == "error" || status["password"] == "error"
          # "absent" means add a record; "error" means the store refused us and
          # adding another record will not help. Do not conflate them.
          Check.new(name: "credentials", status: :warn,
                    detail: "#{secrets.describe}: unreadable (#{secrets.last_error})",
                    hint: "the keychain refused the lookup; walmart login still works manually")
        elsif status["email_present"] && status["password_present"]
          # "present", not "set". doctor deliberately never reads a credential
          # value -- that can prompt for biometrics -- so all it can honestly
          # report is that the items exist. An item can exist and hold nothing,
          # which is what "set" claimed and login then contradicted with "no
          # stored credentials found". Presence is not usability, and the word
          # has to stop short of what the check actually looked at.
          Check.new(name: "credentials", status: :ok,
                    detail: "#{secrets.describe}: email + password present (values not read)")
        elsif status["available"]
          Check.new(name: "credentials", status: :warn,
                    detail: "#{secrets.describe}: incomplete " \
                            "(email #{status["email"]}, password #{status["password"]})",
                    hint: "walmart login works manually; " \
                          "or add: security add-generic-password -s walmart-cli -a email -w")
        else
          Check.new(name: "credentials", status: :warn, detail: secrets.describe,
                    hint: "manual login only on this platform")
        end
      end

      def store_check
        if config.store_pinned?
          store = config.store
          Check.new(name: "store", status: :ok,
                    detail: [store["name"], "(#{store["store_id"]})", store["zip"]]
                            .compact.reject { |part| part.to_s.empty? }.join(" "))
        else
          # The remedy exists now, so it is named. It did not when this was
          # written, and the comment then said why pointing at it would be
          # wrong -- a warn with a remedy that does not exist is worse than a
          # warn with none.
          Check.new(name: "store", status: :warn,
                    detail: "no store pinned", hint: "run: walmart store pin")
        end
      end

      def picker_check
        if Which.find("fzf", env: context.env)
          Check.new(name: "picker", status: :ok, detail: "fzf")
        else
          Check.new(name: "picker", status: :ok, detail: "numbered prompt (install fzf for fuzzy select)")
        end
      end

      # Existence proves a login happened once; only the flock proves one is
      # happening now. See lib/walmart/profile_lock.rb.
      def profile_lock_check
        case ProfileLock.status(paths.profile_lock)
        when :held
          Check.new(name: "profile", status: :warn, detail: "in use: #{paths.profile_lock}",
                    hint: "another walmart command is running; wait for it to finish")
        when :unknown
          Check.new(name: "profile", status: :warn,
                    detail: "could not test the lock: #{paths.profile_lock}",
                    hint: "check permissions on the lock file")
        else
          idle_profile_check
        end
      end

      def idle_profile_check
        return unused_profile_check unless config.fetch("cdp_url").to_s.empty?
        return no_profile_check unless File.directory?(paths.browser_profile)
        return stale_browser_check if stale_browser_lock

        Check.new(name: "profile", status: :ok, detail: paths.browser_profile)
      end

      def no_profile_check
        Check.new(name: "profile", status: :warn, detail: "no browser profile yet",
                  hint: "run: walmart login")
      end

      # Our flock says nobody is using the profile, and Chromium's own lock
      # says otherwise. That combination means a browser outlived the command
      # that started it -- a killed worker, a crash -- and it is the one
      # failure with no `walmart` process in ps for anyone to find. Without
      # this check the next command dies deep inside Playwright, and the good
      # error message two branches up is the one the user never sees.
      def stale_browser_check
        Check.new(name: "profile", status: :warn,
                  detail: "a browser may still be running from an earlier command",
                  hint: "quit any leftover Chromium started by walmart, or remove " \
                        "#{stale_browser_lock} if you are sure none is running")
      end

      def stale_browser_lock
        return @stale_browser_lock if defined?(@stale_browser_lock)

        @stale_browser_lock = SINGLETON_LOCKS
                              .map { |name| File.join(paths.browser_profile, name) }
                              .find { |path| File.exist?(path) || File.symlink?(path) }
      end

      def render(checks)
        if json?
          fmt.emit_json({
                          "version" => VERSION,
                          "ok" => checks.none?(&:fail?),
                          "checks" => checks.map(&:to_h)
                        })
          return
        end

        rows = checks.map do |check|
          { mark: mark(check), name: check.name, detail: check.detail.to_s }
        end
        fmt.table(rows, [[:mark, ""], [:name, "check"], [:detail, "detail"]])

        checks.select(&:hint).each { |check| fmt.hint("  #{check.name}: #{check.hint}") }
      end

      def mark(check)
        case check.status
        when :ok then fmt.paint("ok", :green)
        when :warn then fmt.paint("warn", :yellow)
        else fmt.paint("FAIL", :red)
        end
      end
    end
  end
end
