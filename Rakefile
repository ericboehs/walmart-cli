# frozen_string_literal: true

require "fileutils"

require "rake/testtask"

PYWORKER = File.expand_path("pyworker", __dir__)

Rake::TestTask.new(:test) do |t|
  t.libs << "test" << "lib"
  t.test_files = FileList["test/**/*_test.rb"]
  t.warning = false
end

desc "Run RuboCop"
task :rubocop do
  sh "rubocop"
end

desc "Run the Python worker tests"
task :pytest do
  sh "cd #{PYWORKER} && uv run --quiet pytest"
end

desc "Lint and format-check the Python worker and scripts"
task :ruff do
  sh "cd #{PYWORKER} && uv run --quiet ruff check .."
  sh "cd #{PYWORKER} && uv run --quiet ruff format --check .."
end

desc "Install Python worker dependencies"
task :setup do
  sh "cd #{PYWORKER} && uv sync"
end

desc "Scan the repository for committed secrets"
task :gitleaks do
  sh "gitleaks detect --no-banner --redact" do |ok, _res|
    warn("gitleaks not installed or found findings; see output above") unless ok
  end
end

desc "Run the CLI end to end against a stub worker and check the bytes"
task :smoke do
  sh "ruby #{File.expand_path("script/smoke", __dir__)}"
end

# Not in ci: it runs the suite once per mutant, and it edits tracked files while
# it works. It is the check you run when you have changed a guard, and the one
# that answers "would anything have noticed?".
desc "Break each guard on purpose and check some suite fails"
task :mutants do
  script = File.expand_path("script/mutate", __dir__)
  plan = File.expand_path("script/mutants.json", __dir__)
  sh "ruby #{script} --plan #{plan} -- rake test"
end

desc "Everything CI runs"
task ci: %i[rubocop ruff test pytest smoke]

task default: :ci

desc "install the git hooks that run the gates before a commit"
task :hooks do
  hooks = File.join(__dir__, ".git", "hooks")
  FileUtils.mkdir_p(hooks)
  source = File.join(__dir__, "script", "hooks", "pre-commit")
  target = File.join(hooks, "pre-commit")
  FileUtils.cp(source, target)
  FileUtils.chmod(0o755, target)
  puts "installed #{target}"
end
