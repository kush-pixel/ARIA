import json, os, subprocess, sys

raw = os.environ.get('CLAUDE_TOOL_OUTPUT', '{}')
try:
    data = json.loads(raw)
    fp = data.get('file_path') or data.get('path', '')
except Exception:
    sys.exit(0)

if not fp:
    sys.exit(0)

project_dir = os.environ.get('CLAUDE_PROJECT_DIR', '.')
abs_path = os.path.join(project_dir, fp) if not os.path.isabs(fp) else fp

if not os.path.exists(abs_path):
    sys.exit(0)

if abs_path.endswith('.py'):
    subprocess.run(['ruff', 'format', abs_path], cwd=project_dir, capture_output=True)
    subprocess.run(['ruff', 'check', abs_path, '--fix', '--quiet'], cwd=project_dir, capture_output=True)

sys.exit(0)
