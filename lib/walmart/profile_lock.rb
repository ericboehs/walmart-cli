# frozen_string_literal: true

module Walmart
  # Liveness of the browser-profile advisory lock.
  #
  # The lock *file* outlives the lock. pyworker/browser.py creates it with
  # O_CREAT and releases only the flock, so after the first `walmart login` the
  # path exists forever. Treating existence as "in use" would make `doctor` --
  # the one command whose entire job is an accurate answer -- warn about a
  # process that finished last week.
  #
  # So we ask the kernel instead: take the lock non-blockingly and give it
  # straight back. Holding it for those few microseconds can make a worker
  # starting at the same instant report the profile as busy; that is a far
  # smaller lie than a permanent phantom warning, and it self-corrects on retry.
  module ProfileLock
    module_function

    # :held    - some other process holds the lock right now
    # :free    - nobody holds it (whether or not the file exists)
    # :unknown - we could not tell, and must not claim either way
    def status(path)
      return :free unless File.exist?(path)

      File.open(path, File::RDWR) do |file|
        file.flock(File::LOCK_EX | File::LOCK_NB) ? release(file) : :held
      end
    rescue Errno::ENOENT
      # Released and removed between the check and the open.
      :free
    rescue SystemCallError, IOError
      :unknown
    end

    def held?(path) = status(path) == :held

    def release(file)
      file.flock(File::LOCK_UN)
      :free
    end
  end
end
