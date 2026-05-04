import subprocess, os

project_dir = os.environ.get('CLAUDE_PROJECT_DIR', '.')
try:
    changed = subprocess.check_output(
        ['git', 'diff', '--name-only'],
        cwd=project_dir, text=True, stderr=subprocess.DEVNULL
    ).strip()
    untracked = subprocess.check_output(
        ['git', 'ls-files', '--others', '--exclude-standard'],
        cwd=project_dir, text=True, stderr=subprocess.DEVNULL
    ).strip()
except Exception:
    changed, untracked = '', ''

print('\n=== ARIA SESSION COMPLETE ===')
if changed:
    print(f'Modified:\n{changed}')
if untracked:
    print(f'New files:\n{untracked}')
print('''
Next steps:
  1. Review: git diff
  2. Test:   cd backend && python -m pytest tests/ -v -m "not integration"
  3. Lint:   ruff check app/
  4. Stage:  git add <specific files>
  5. Commit: git commit -m "feat(service): description"

DO NOT git push — user runs push manually.
''')
