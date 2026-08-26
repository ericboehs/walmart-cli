# frozen_string_literal: true

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

desc "Everything CI runs"
task ci: %i[rubocop ruff test pytest]

task default: :ci
