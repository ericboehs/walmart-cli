# frozen_string_literal: true

require_relative "browser"

module Walmart
  module Commands
    # The `doctor` checks that describe attached mode.
    #
    # Separate from doctor.rb because attachment is the one mode that changes
    # what another check *means*: while attached, the launched profile is not
    # used, and reporting it as missing would be advice for a mode we are not
    # in. Keeping the pair together makes that coupling visible.
    module AttachmentCheck
      # Doctor::Check rather than Check: this module is included into Doctor, so
      # the constant is not in lexical scope here, only in Doctor's. It resolves
      # at call time, by which point doctor.rb has finished loading.

      private

      # Attached mode is invisible in every other line of this report, which is
      # how a person can configure cdp_url, read a page of green ticks, and
      # learn nothing about the thing they just set up. It also changes what the
      # profile line below means: attached, that profile is not used at all.
      def attachment_check
        url = config.fetch("cdp_url")
        return not_attached_check if url.to_s.empty?

        version = attachment_version(url)
        return attached_check(url, version) if version

        Doctor::Check.new(name: "attachment", status: :warn,
                          detail: "cdp_url is set but nothing is listening on #{url}",
                          hint: "run: walmart browser start")
      end

      # Separated so a test can stand in for the endpoint without opening a
      # socket, and so this file has one place that knows how the fact is got.
      def attachment_version(url)
        Browser.new(context).reachable_version(url)
      end

      def not_attached_check
        Doctor::Check.new(name: "attachment", status: :ok,
                          detail: "not attached (a browser is launched per command)")
      end

      def attached_check(url, version)
        Doctor::Check.new(name: "attachment", status: :ok, detail: "attached to #{url} (#{version})")
      end

      # Saying "no browser profile yet -- run walmart login" while attached would
      # be advice for a mode we are not in.
      def unused_profile_check
        Doctor::Check.new(name: "profile", status: :ok,
                          detail: "not used while attached to your own browser")
      end
    end
  end
end
