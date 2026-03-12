# opencode-orchestrator v2

MCP server that lets Claude Code act as a **team lead**, delegating coding tasks
to [OpenCode](https://github.com/opencode-ai/opencode) agents running any LLM
(Kimi, GLM, GPT-4o, Qwen, etc.).

## What's new in v2

v1 used PTY capture to scrape terminal output from OpenCode's TUI — fragile,
lossy, and impossible to monitor. v2 replaces all of that with:

- **File-based communication** — spec file in, result file out. No more ANSI stripping.
- **Tmux-based execution** — one session (`oc-tasks`), each task is a window/tab.
  Watch your agents work in real-time with `tmux attach -t oc-tasks`.
- **Plugin architecture** — swap OpenCode for Aider or any other coding agent.
- **Blocking-first API** — `run_task` does everything in one call.
- **7 focused tools** instead of 9, with clearer responsibilities.

## Requirements

- Python 3.11+
- [OpenCode](https://github.com/opencode-ai/opencode) installed and on PATH
- tmux (recommended, falls back to subprocess if unavailable)
- [uv](https://github.com/astral-sh/uv) (recommended for running)

## Installation

```bash
# Clone or copy the opencode-orchestrator directory into your project
cd opencode-orchestrator

# Install with uv (recommended)
uv pip install -e .

# Or with pip
pip install -e .
```

## Setup with Claude Code

Add to your Claude Code MCP config (`~/.claude/mcp.json` or project-level):

```json
{
  "mcpServers": {
    "opencode-orchestrator": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/opencode-orchestrator", "opencode-orchestrator"]
    }
  }
}
```

Or if installed globally:

```json
{
  "mcpServers": {
    "opencode-orchestrator": {
      "command": "opencode-orchestrator"
    }
  }
}
```

Restart Claude Code after adding the config. You should see the 7 tools available.

## Quick Start

### 1. Basic usage — one task

The simplest way to delegate work. Claude Code calls `run_task`, which blocks
until the agent finishes and returns the result:

```python
run_task(
    task_id="task-hello",
    spec="""
## Project Context
Node.js project with src/ directory.

## Deliverable
Create src/hello.ts that exports a greet(name) function returning "Hello, {name}!".

## Acceptance Criteria
- [ ] Function exported correctly
- [ ] Works with any string input
""",
    cwd="/home/user/my-project",
)
```

What happens behind the scenes:

1. Creates `.tasks/task-hello.md` with your spec + result instructions
2. Creates git branch `task/task-hello`
3. Opens a tmux window named `task-hello` inside session `oc-tasks`
4. Runs `opencode run --dir /home/user/my-project "Read .tasks/task-hello.md and ..."`
5. Waits for the tmux window to close (= agent finished)
6. Reads `.tasks/task-hello.result.md`
7. Returns JSON with status, result content, files changed, duration

### 2. Watch your agents work

While a task is running, open another terminal:

```bash
# Attach to the team control room
tmux attach -t oc-tasks

# Inside tmux:
# Ctrl-b w       → list all task windows (like tabs)
# Ctrl-b n / p   → next / previous task
# Ctrl-b 0-9     → jump to window by number
# Ctrl-b d       → detach back to terminal
```

Or from Claude Code, use `peek_task`:

```python
peek_task("task-hello")
# Returns the last 100 lines of what the agent's terminal shows
```

### 3. Parallel tasks

For independent features that can run simultaneously:

```python
# Start both tasks (returns immediately)
start_task("task-auth",     spec=auth_spec,     cwd="/project", isolation="worktree")
start_task("task-payments", spec=payments_spec, cwd="/project", isolation="worktree")

# Wait for both to finish
wait_tasks(["task-auth", "task-payments"], timeout=600)

# Read results
get_result("task-auth")
get_result("task-payments")
```

Parallel tasks **must** use `isolation="worktree"` — each agent needs its own
copy of the file system to avoid conflicts.

### 4. Code review and feedback

If the result isn't satisfactory:

```python
send_feedback("task-auth", """
## Issues
1. Missing error handling for expired tokens
2. No test for malformed Authorization header

## Required Changes
- Add try/catch in middleware for TokenExpiredError
- Add 2 test cases
""")
```

This writes `.tasks/task-auth.feedback.md` and notifies the agent if still running.

## MCP Tools Reference

| Tool | Blocking | Description |
|------|----------|-------------|
| **`run_task`** | ✅ | Run a task synchronously. **Primary tool — use 90% of the time.** |
| `start_task` | ❌ | Start a task asynchronously. For parallel work. |
| `wait_tasks` | ✅ | Block until all specified tasks complete. |
| `get_result` | ❌ | Read the `.result.md` file of a completed task. |
| `peek_task` | ❌ | See what an agent is currently doing (tmux capture or log). |
| `send_feedback` | ❌ | Write feedback file + notify running agent. |
| `list_tasks` | ❌ | List all tasks, optionally filter by status. |

### run_task / start_task parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `task_id` | str | required | Unique task ID, e.g. `"task-auth"` |
| `spec` | str | required | Markdown task specification |
| `cwd` | str | required | Project root directory (absolute path) |
| `isolation` | str | `"branch"` | `"none"`, `"branch"`, or `"worktree"` |
| `model` | str | `None` | Model override, e.g. `"kimi/moonshot-v1-8k"` |
| `agent` | str | `"opencode"` | Agent plugin name |
| `timeout` | int | `600` | Max seconds to wait (run_task only) |
| `visible` | bool | `true` | tmux window (true) or headless subprocess (false) |

## File-based Communication

All leader ↔ teammate communication goes through `.tasks/` in the project root:

```
project/
├── .tasks/                        ← add to .gitignore
│   ├── task-auth.md               # Spec (auto-generated from your spec param)
│   ├── task-auth.result.md        # Result report (teammate writes this)
│   ├── task-auth.log              # Terminal output log
│   └── task-auth.feedback.md      # Your review feedback (optional)
├── src/
│   └── ...
```

### Spec file

Written automatically by `run_task` / `start_task`. Your `spec` parameter becomes
the body, and a "When Done" section is appended with instructions for the teammate
to write the result file.

### Result file

The teammate writes this when done. Format:

```markdown
# Result: task-auth

## Status: DONE

## Summary
Implemented JWT auth middleware and login route.

## Files Changed
- src/auth/middleware.ts (created — 45 lines)
- src/auth/routes.ts (created — 62 lines)

## Test Results
✅ 8 passed, 0 failed

## Issues
None.

## Git
Committed on branch task/task-auth
```

## Git Isolation

| Mode | When to use | What happens |
|------|-------------|--------------|
| `"branch"` (default) | Sequential tasks | Creates branch `task/<id>`, agent checks it out |
| `"worktree"` | Parallel tasks | Creates git worktree at `/tmp/oc-worktree-<id>` |
| `"none"` | Quick fixes | Works on current branch, no isolation |

After a task completes, you merge the branch:

```bash
git merge task/task-auth
```

For worktree mode, the worktree is auto-cleaned but the branch is kept for merging.

## Plugin Architecture

The orchestrator is agent-agnostic. OpenCode is the default, but you can add
any coding agent by implementing the `AgentPlugin` interface:

```python
# src/opencode_orchestrator/agents/my_agent.py
from .base import AgentPlugin

class MyAgent(AgentPlugin):
    name = "my-agent"

    def build_command(self, cwd, prompt, model=None):
        return ["my-agent", "run", "--dir", cwd, prompt]

    def build_env(self, model=None):
        import os
        return os.environ.copy()

    @property
    def supports_prompt_file(self):
        return False
```

Then register it in `agents/__init__.py`:

```python
from .my_agent import MyAgent

_REGISTRY = {
    "opencode": OpenCodeAgent,
    "my-agent": MyAgent,  # ← add here
}
```

Use it: `run_task(..., agent="my-agent")`

## Troubleshooting

### "opencode binary not found"

Make sure OpenCode is installed and on your PATH:
```bash
which opencode
opencode --version
```

### tmux permission denied

If tmux can't create its socket (common in sandboxed environments), the
orchestrator automatically falls back to plain subprocess execution. Everything
still works — you just can't attach to watch agents live. Check logs via:
```bash
tail -f .tasks/task-auth.log
```

### Agent doesn't write result file

This can happen with weaker models. The orchestrator handles it gracefully:
- If `.result.md` is missing, it reads the `.log` file as fallback
- The response includes a `warning` field explaining what happened
- You can re-run the task or implement it yourself

### Task times out

Default timeout is 600 seconds (10 minutes). For larger tasks:
```python
run_task(..., timeout=1200)  # 20 minutes
```

If a task times out, the orchestrator still checks for the result file —
the agent may have finished the work but not exited cleanly.

## Project Structure

```
opencode-orchestrator/
├── pyproject.toml
├── README.md
├── ARCHITECTURE-V2.md              # Detailed design document
├── SKILL.md                        # Claude Code skill file
└── src/opencode_orchestrator/
    ├── __init__.py                 # v2.0.0
    ├── server.py                   # 7 MCP tool definitions
    ├── models.py                   # Task, TaskStatus, IsolationMode
    ├── task_manager.py             # Core: spec files, execution, result collection
    ├── tmux.py                     # Tmux session/window management
    └── agents/
        ├── __init__.py             # Plugin registry
        ├── base.py                 # AgentPlugin ABC (interface)
        └── opencode.py             # OpenCode plugin
```

## License

MIT
