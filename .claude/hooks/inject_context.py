import subprocess, os

project_dir = os.environ.get('CLAUDE_PROJECT_DIR', '.')
try:
    branch = subprocess.check_output(
        ['git', 'branch', '--show-current'],
        cwd=project_dir, text=True, stderr=subprocess.DEVNULL
    ).strip()
    log = subprocess.check_output(
        ['git', 'log', '--oneline', '-5'],
        cwd=project_dir, text=True, stderr=subprocess.DEVNULL
    ).strip()
except Exception:
    branch, log = 'unknown', 'no history'

print(f'''
=== ARIA v4.3 CONTEXT ===
Branch: {branch}
Recent commits:
{log}

ABSOLUTE RULES:
1. NEVER git push, commit, or add — user runs all git commands
2. NEVER recommend specific medications in any output
3. Briefing language: possible adherence concern not non-adherent
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
''')
