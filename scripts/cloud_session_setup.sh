#!/bin/bash
# Prepare a Claude Code cloud session: install dependencies so tests run immediately.
# Runs from the SessionStart hook in .claude/settings.json. Does nothing outside the cloud.
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi
cd "$CLAUDE_PROJECT_DIR" || exit 1
# Replay tests read historical commits; deepen a shallow clone first.
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
  git fetch --quiet --unshallow origin 2>/dev/null || git fetch --quiet --depth=2000 origin 2>/dev/null || true
fi
# pip skips requirements that are already satisfied, so resumes stay fast.
pip install -q -r requirements.txt pytest >/dev/null 2>&1 || echo "cloud_session_setup: dependency install failed; run 'pip install -q -r requirements.txt pytest' manually" >&2
exit 0
