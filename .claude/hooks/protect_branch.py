import json, os, sys, re

raw = os.environ.get('CLAUDE_TOOL_INPUT', '{}')
try:
    cmd = json.loads(raw).get('command', '')
except Exception:
    sys.exit(0)

blocked = [
    (r'git\s+push',         'git push is not permitted. User runs all push commands.'),
    (r'git\s+commit',       'git commit is not permitted. User runs all commit commands.'),
    (r'git\s+merge',        'git merge is not permitted. User runs this command.'),
    (r'git\s+rebase',       'git rebase is not permitted. User runs this command.'),
    (r'git\s+reset\s+--hard', 'git reset --hard is not permitted.'),
    (r'rm\s+-rf',           'rm -rf is not permitted.'),
]

for pattern, message in blocked:
    if re.search(pattern, cmd):
        print(f'BLOCKED: {message}', file=sys.stderr)
        sys.exit(2)

sys.exit(0)
