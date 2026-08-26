# frozen_string_literal: true

module Walmart
  # Executable lookup with an injectable environment.
  #
  # Three places needed this (worker, secrets, doctor) and three copies drifted
  # immediately. One copy, and tests can hand it a PATH that does not exist on
  # the machine running them.
  module Which
    module_function

    def find(command, env: ENV)
      return nil if command.nil? || command.empty?

      env.fetch("PATH", "").split(File::PATH_SEPARATOR).each do |dir|
        next if dir.empty?

        candidate = File.join(dir, command)
        return candidate if File.executable?(candidate) && !File.directory?(candidate)
      end
      nil
    end
  end
end
