# frozen_string_literal: true

require_relative "../test_helper"

module Walmart
  # The lock file outlives the lock, so existence answers the wrong question.
  class ProfileLockTest < TestCase
    def test_a_missing_lock_file_is_free
      assert_equal :free, ProfileLock.status(paths.profile_lock)
    end

    # This is the state every machine is in after its first login: the file is
    # there forever, and nothing is running.
    def test_a_leftover_lock_file_is_free
      touch_lock

      assert_equal :free, ProfileLock.status(paths.profile_lock)
      refute ProfileLock.held?(paths.profile_lock)
    end

    def test_a_held_lock_is_held
      touch_lock
      while_locked do
        assert_equal :held, ProfileLock.status(paths.profile_lock)
        assert ProfileLock.held?(paths.profile_lock)
      end
    end

    def test_a_released_lock_reads_as_free_again
      touch_lock
      while_locked { nil }

      assert_equal :free, ProfileLock.status(paths.profile_lock)
    end

    # Checking must not become taking: a doctor run that stole the lock would
    # break the very command it is diagnosing.
    def test_checking_gives_the_lock_straight_back
      touch_lock
      3.times { ProfileLock.status(paths.profile_lock) }

      acquired = nil
      while_locked { |file| acquired = file }

      refute_nil acquired
    end

    def test_an_unopenable_lock_is_unknown_rather_than_a_guess
      touch_lock
      File.chmod(0o000, paths.profile_lock)

      begin
        assert_equal :unknown, ProfileLock.status(paths.profile_lock)
      ensure
        File.chmod(0o600, paths.profile_lock)
      end
    end

    private

    # Holds the lock the way a running worker does, from a separate open file
    # description, so the flock genuinely conflicts.
    def while_locked
      File.open(paths.profile_lock, "r+") do |file|
        assert file.flock(File::LOCK_EX | File::LOCK_NB), "could not take the lock to set up the test"
        begin
          yield file
        ensure
          file.flock(File::LOCK_UN)
        end
      end
    end

    def touch_lock
      FileUtils.mkdir_p(File.dirname(paths.profile_lock))
      File.write(paths.profile_lock, "12345")
    end
  end
end
