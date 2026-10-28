@AGENTS.md

## Claude Code only

- Detailed reference: `docs/ARCHITECTURE.md` (the former long CLAUDE.md content moved there unchanged).
- Start non-trivial work in plan mode and confirm scope against the linked Issue.
- gstack: when the gstack skills under `.claude/skills/` are available, use `/browse` for all web browsing and never use `mcp__claude-in-chrome__*` tools. Other gstack skills (`/review`, `/qa`, `/investigate`, `/codex`, `/cso`, `/retro`, …) describe themselves when invoked.
