### inject_context.py
### SAVE AS: .claude/hooks/inject_context.py

import subprocess, os

project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
try:
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"],
        cwd=project_dir, text=True, stderr=subprocess.DEVNULL
    ).strip()
    log = subprocess.check_output(
        ["git", "log", "--oneline", "-5"],
        cwd=project_dir, text=True, stderr=subprocess.DEVNULL
    ).strip()
except Exception:
    branch, log = "unknown", "no history"

print(f"""
=== ARIA v4.3 CONTEXT ===
Branch: {branch}
Recent commits:
{log}

ABSOLUTE RULES:
1. NEVER git push, commit, or add — user runs all git commands
2. NEVER recommend specific medications in any output
3. Briefing language: "possible adherence concern" not "non-adherent"
4. SQLAlchemy 2.0 ASYNC only — no session.query()
5. Pydantic v2 — model_config = SettingsConfigDict
6. ruff check app/ must pass before reporting complete
7. THREE-LAYER AI: Layer 1 rules -> Layer 2 risk score -> Layer 3 LLM
8. Layer 3 NEVER runs before Layer 1 is complete and verified
9. Synthetic data SD must be 8-12 mmHg (never flat)
10. Device outage = absent rows, never null values
11. Every bundle_import, reading_ingested, briefing_viewed must audit_events
12. risk_score stored on patients table — sort dashboard by tier then score

DATABASE: PostgreSQL via Supabase (asyncpg driver)
LLM MODEL: claude-sonnet-4-20250514 (Layer 3 only)
=== END ===
""")


### protect_branch.py
### SAVE AS: .claude/hooks/protect_branch.py

import json, os, sys, re

raw = os.environ.get("CLAUDE_TOOL_INPUT", "{}")
try:
    cmd = json.loads(raw).get("command", "")
except Exception:
    sys.exit(0)

blocked = [
    (r"git\s+push", "git push is not permitted. User runs all push commands."),
    (r"git\s+commit", "git commit is not permitted. User runs all commit commands."),
    (r"git\s+merge", "git merge is not permitted. User runs this command."),
    (r"git\s+rebase", "git rebase is not permitted. User runs this command."),
    (r"git\s+reset\s+--hard", "git reset --hard is not permitted."),
    (r"rm\s+-rf", "rm -rf is not permitted."),
]

for pattern, message in blocked:
    if re.search(pattern, cmd):
        print(f"BLOCKED: {message}", file=sys.stderr)
        sys.exit(2)

sys.exit(0)


### format_and_lint.py
### SAVE AS: .claude/hooks/format_and_lint.py

import json, os, subprocess, sys

raw = os.environ.get("CLAUDE_TOOL_OUTPUT", "{}")
try:
    data = json.loads(raw)
    fp = data.get("file_path") or data.get("path", "")
except Exception:
    sys.exit(0)

if not fp:
    sys.exit(0)

project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
abs_path = os.path.join(project_dir, fp) if not os.path.isabs(fp) else fp

if not os.path.exists(abs_path):
    sys.exit(0)

if abs_path.endswith(".py"):
    subprocess.run(
        ["ruff", "format", abs_path],
        cwd=project_dir, capture_output=True
    )
    subprocess.run(
        ["ruff", "check", abs_path, "--fix", "--quiet"],
        cwd=project_dir, capture_output=True
    )

sys.exit(0)


### session_summary.py
### SAVE AS: .claude/hooks/session_summary.py

import subprocess, os

project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
try:
    changed = subprocess.check_output(
        ["git", "diff", "--name-only"],
        cwd=project_dir, text=True, stderr=subprocess.DEVNULL
    ).strip()
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=project_dir, text=True, stderr=subprocess.DEVNULL
    ).strip()
except Exception:
    changed, untracked = "", ""

print("\n=== ARIA SESSION COMPLETE ===")
if changed:
    print(f"Modified:\n{changed}")
if untracked:
    print(f"New files:\n{untracked}")
print("""
Next steps:
  1. Review: git diff
  2. Test:   cd backend && python -m pytest tests/ -v -m "not integration"
  3. Lint:   ruff check app/
  4. Stage:  git add <specific files>
  5. Commit: git commit -m "feat(service): description"

DO NOT git push — user runs push manually.
""")
