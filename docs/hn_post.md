# Hacker News: Show HN Post

## Title

Show HN: MCP server that gives Claude Code structured traces from Codex CLI (GPT-5.4)

## URL

https://github.com/tsunamayo7/claude-code-codex-agents

## Text

I built an MCP server that lets Claude Code delegate tasks to OpenAI's Codex CLI and get back structured execution traces instead of raw text.

The problem: Codex CLI outputs a wall of JSONL events. If you pipe that into Claude Code, it has no idea what tools were called, which files changed, or whether the task actually succeeded.

claude-code-codex-agents parses the full JSONL event stream and returns a structured report: tool calls with status, files touched, execution time, and errors. Claude Code can then make informed decisions about what to do next.

Key technical details:

- Parses Codex CLI's JSONL event stream (tool_call, file_op, error events) into a typed CodexTrace dataclass
- Supports up to 6 parallel subprocess executions
- threadId persistence for multi-turn sessions
- 3-tier sandbox model (read-only / workspace-write / danger-full-access)
- ANSI/OSC escape sanitization to prevent terminal injection
- Single file (~820 lines), zero external deps beyond FastMCP
- 56 tests covering parsing, security, and edge cases

The "adversarial review" tool is useful: Claude writes code, GPT-5.4 reviews it from a different angle, eliminating single-model blind spots.

MIT licensed. Python 3.12+, requires Codex CLI (`npm install -g @openai/codex`).
