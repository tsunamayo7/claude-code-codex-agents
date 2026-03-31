# Reddit Posts for helix-codex

---

## r/ClaudeAI

**Title:** I built an MCP bridge that gives Claude Code structured traces from Codex CLI (GPT-5.4)

**Body:**

I got tired of Claude Code burning through my 5-hour limit on tasks that a cheaper model could handle. So I built [helix-codex](https://github.com/tsunamayo7/helix-codex) -- an MCP server that lets Claude Code delegate work to Codex CLI (GPT-5.4) and get back **structured execution reports**, not raw text dumps.

**What makes it different from the 6+ other Codex bridges:**

Every other bridge is a thin wrapper -- call Codex, get text back. helix-codex parses the **entire JSONL event stream** and returns which tools were used, which files were touched, timing, and errors. Claude actually knows what happened.

**Real numbers from my setup:**

- `explain` tool: 5.4s for a full code explanation
- `review` tool: 15.7s for CRITICAL/WARNING/INFO classified review
- `parallel_execute`: 3 tasks running simultaneously
- `session_continue`: pick up where you left off via threadId

The best part: I ran Claude Agent + Codex in parallel to compare singleton patterns. Codex came back with an `lru_cache` approach that Claude hadn't considered. Two models > one model.

**Other highlights:** up to 6 parallel tasks, adversarial review loop (GPT-5.4 challenges Claude's code), terminal injection prevention, 56 tests.

GitHub: https://github.com/tsunamayo7/helix-codex

---

## r/ClaudeCode

**Title:** MCP server to offload tasks to Codex CLI -- full JSONL parsing, not just a wrapper

**Body:**

If you're hitting context limits on complex prompts, I built [helix-codex](https://github.com/tsunamayo7/helix-codex) to offload work to Codex CLI (GPT-5.4) via MCP.

**The problem with existing bridges:** they pipe text in and text out. Claude has no idea what tools Codex used, what files it touched, or if it even succeeded. helix-codex parses every JSONL event from Codex and returns a structured trace:

```
[Codex gpt-5.4] Completed in 8.3s
Tools used: read_file, edit_file, shell
Files touched: src/auth.py
```

**What I actually use it for:**

- **Code review** (`review` tool): 15.7s, returns findings classified as CRITICAL/WARNING/INFO
- **Parallel execution**: run up to 6 Codex tasks at once
- **Cross-model discussion**: get GPT-5.4's take on your design decisions
- **Adversarial review loop**: GPT-5.4 challenges Claude's code from a different angle
- **Session continuity**: threadId persistence so Codex remembers context

One thing that surprised me: running both models on the same problem produces genuinely different solutions. Asked both about singleton patterns -- Codex suggested `lru_cache`, which was a creative alternative.

56 tests, sandbox security with 3-tier policy, terminal injection prevention.

GitHub: https://github.com/tsunamayo7/helix-codex

Setup is just `uv tool install` + add to `claude_desktop_config.json`.
