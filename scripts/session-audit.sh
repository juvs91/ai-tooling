#!/bin/bash
# session-audit.sh — Summarize what a model did in a CC session
# Usage: bash ~/ai-tooling/scripts/session-audit.sh /path/to/project [session-id-prefix]
PROJECT="${1:-$(pwd)}"
# CC slugifies by replacing every / with - (leading / becomes leading -)
PROJECT_SLUG=$(echo "$PROJECT" | sed 's|/|-|g')
SESSION_DIR="$HOME/.claude/projects/$PROJECT_SLUG"

if [ ! -d "$SESSION_DIR" ]; then
  echo "No CC session dir found for: $PROJECT"
  echo "Expected: $SESSION_DIR"
  exit 1
fi

if [ -n "$2" ]; then
  SESSION_FILE=$(ls "$SESSION_DIR/${2}"*.jsonl 2>/dev/null | head -1)
else
  SESSION_FILE=$(ls -t "$SESSION_DIR"/*.jsonl 2>/dev/null | head -1)
fi

[ -z "$SESSION_FILE" ] && { echo "No session found in $SESSION_DIR"; exit 1; }

echo "Session: $(basename $SESSION_FILE)"
echo "Project: $PROJECT"
echo "Size:    $(wc -c < "$SESSION_FILE" | awk '{printf "%.1f MB", $1/1048576}') / $(wc -l < "$SESSION_FILE") lines"
echo ""

python3 - "$SESSION_FILE" <<'PYEOF'
import json, sys
from collections import Counter

edits, bash_cmds, test_runs = [], [], []
tool_counts = Counter()
total_tools = 0

with open(sys.argv[1]) as f:
    for line in f:
        try:
            entry = json.loads(line)
        except Exception:
            continue
        msg = entry.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                inp = block.get("input", {})
                tool_counts[name] += 1
                total_tools += 1
                if name in ("Edit", "Write", "MultiEdit"):
                    path = inp.get("file_path", "?")
                    edits.append(path)
                if name == "Bash":
                    cmd = inp.get("command", "")[:100].replace("\n", " ")
                    bash_cmds.append(cmd)
                    if any(t in cmd for t in ("test:run", "vitest", "pytest", "jest")):
                        test_runs.append(cmd[:80])

print(f"=== Files Modified ({len(set(edits))} unique) ===")
seen = set()
for f in edits:
    if f not in seen:
        seen.add(f)
        print(f"  {f}")

print(f"\n=== Test Runs ({len(test_runs)}) ===")
for t in test_runs:
    print(f"  {t}")

print(f"\n=== Bash Commands ({len(bash_cmds)} total, unique) ===")
seen_bash = set()
for c in bash_cmds:
    if c not in seen_bash:
        seen_bash.add(c)
        print(f"  {c}")
    if len(seen_bash) >= 25:
        remaining = len(bash_cmds) - sum(1 for _ in seen_bash)
        print(f"  ... ({len(bash_cmds) - 25} more)")
        break

print(f"\n=== Tool Call Distribution ({total_tools} total) ===")
for tool, count in tool_counts.most_common():
    bar = "█" * min(count, 40)
    print(f"  {count:4d}x  {tool:<30} {bar}")
PYEOF
