"""opencode-orchestrator v2 MCP Server.

Exposes 9 tools for Claude Code (leader) to delegate coding tasks
to OpenCode agents (teammates) via file-based communication and tmux.

Tools:
    run_task        — Run a task synchronously (blocking). Primary tool.
    start_task      — Start a task asynchronously. For parallel work.
    wait_tasks      — Block until all specified tasks complete.
    get_result      — Read the result file of a completed task.
    peek_task       — See what an agent is currently doing.
    send_feedback   — Send review feedback to a task.
    list_tasks      — List all tasks with status.
    get_progress    — Read the progress tracking file.
    list_profiles   — List available specialty profiles.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .task_manager import TaskManager
from . import profiles as profile_module

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

mcp = FastMCP("opencode-orchestrator")
_mgr = TaskManager()


# ── Primary tool ──────────────────────────────────────────────────────────────


@mcp.tool()
def run_task(
    task_id: str,
    cwd: str,
    spec: str = "",
    spec_file: Optional[str] = None,
    profile: Optional[str] = None,
    isolation: str = "branch",
    model: Optional[str] = None,
    agent: str = "opencode",
    timeout: int = 600,
    visible: bool = True,
) -> str:
    """Run a coding task synchronously. Blocks until the agent completes.

    This is the PRIMARY tool — use it for most tasks. Sets up git isolation,
    spawns the agent in a tmux window, waits for completion, reads the result
    file, and returns everything in one call.

    PREFERRED: Write the spec to .tasks/<task_id>.md yourself first, then pass
    spec_file=".tasks/<task_id>.md". This keeps specs visible and reviewable.

    Args:
        task_id:   Unique ID (e.g. "task-auth"). Used for filenames and tmux window name.
        cwd:       Project root directory (absolute path).
        spec:      (ALTERNATIVE) Inline task spec in markdown.
        spec_file: (PREFERRED) Path to pre-written spec file (absolute or relative to cwd).
        profile:   Specialty profile for the teammate (e.g. "be-dev", "fe-dev", "qa",
                   "ui-review", "devops", "security"). Injects role context into spec and prompt.
                   Use list_profiles() to see all available profiles.
        isolation: Git isolation mode: "branch" (default), "worktree", "none".
        model:     Optional model override. If not set and profile has default_model, uses that.
        agent:     Agent plugin to use (default: "opencode").
        timeout:   Max seconds to wait (default: 600).
        visible:   If true (default), run in tmux window (user can: tmux attach -t oc-tasks).

    Returns:
        JSON with: ok, status, result, branch, files_changed, duration_s, profile
    """
    try:
        resolved_spec_file = spec_file
        if spec_file and not os.path.isabs(spec_file):
            resolved_spec_file = os.path.join(cwd, spec_file)

        result = _mgr.run_sync(
            task_id=task_id,
            spec=spec,
            cwd=cwd,
            isolation=isolation,
            model=model,
            agent=agent,
            timeout=timeout,
            visible=visible,
            spec_file=resolved_spec_file,
            profile=profile,
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


# ── Async workflow ────────────────────────────────────────────────────────────


@mcp.tool()
def start_task(
    task_id: str,
    cwd: str,
    spec: str = "",
    spec_file: Optional[str] = None,
    profile: Optional[str] = None,
    isolation: str = "branch",
    model: Optional[str] = None,
    agent: str = "opencode",
    visible: bool = True,
) -> str:
    """Start a coding task asynchronously. Returns immediately.

    Use for parallel work: call start_task() multiple times, then wait_tasks().

    Args: same as run_task (except no timeout — uses default 600s internally).
    """
    try:
        resolved_spec_file = spec_file
        if spec_file and not os.path.isabs(spec_file):
            resolved_spec_file = os.path.join(cwd, spec_file)

        result = _mgr.start_async(
            task_id=task_id,
            spec=spec,
            cwd=cwd,
            isolation=isolation,
            model=model,
            agent=agent,
            visible=visible,
            spec_file=resolved_spec_file,
            profile=profile,
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@mcp.tool()
def wait_tasks(task_ids: list[str], timeout: int = 600) -> str:
    """Block until all specified tasks complete (or timeout).

    Use after calling start_task() for parallel tasks.

    Args:
        task_ids: List of task IDs to wait for.
        timeout:  Max seconds to wait (default: 600).

    Returns:
        JSON with per-task status, all_done flag, and any_failed flag.
    """
    try:
        result = _mgr.wait_tasks(task_ids=task_ids, timeout=timeout)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


# ── Result & monitoring ───────────────────────────────────────────────────────


@mcp.tool()
def get_result(task_id: str) -> str:
    """Read the result of a completed task.

    Returns the full content of .tasks/<task_id>.result.md.
    If no result file exists, returns the log tail as fallback.
    """
    result = _mgr.get_result(task_id)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def peek_task(task_id: str) -> str:
    """See what an agent is currently doing.

    If running in tmux (visible=true): captures the last 100 lines of the tmux pane.
    If headless (visible=false): reads the last 3000 chars of the log file.

    Use this to check progress on long-running tasks without waiting for completion.
    """
    result = _mgr.peek(task_id)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def send_feedback(task_id: str, feedback: str) -> str:
    """Send review feedback to a task.

    Writes feedback to .tasks/<task_id>.feedback.md. If the agent is still
    running in tmux, also sends a notification keystroke to alert the agent.

    Args:
        task_id:  The task to send feedback to.
        feedback: Markdown content describing issues and what needs to change.
    """
    result = _mgr.do_send_feedback(task_id=task_id, feedback=feedback)
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def list_tasks(status: Optional[str] = None) -> str:
    """List all tasks with their current status.

    Args:
        status: Optional filter — "pending", "running", "done", "failed", "timeout"

    Returns:
        JSON array of task objects with status, duration, result preview, etc.
    """
    tasks = _mgr.list_all(status_filter=status)
    return json.dumps(
        {"ok": True, "count": len(tasks), "tasks": tasks},
        indent=2,
        ensure_ascii=False,
    )


# ── Progress tracking ────────────────────────────────────────────────────────


@mcp.tool()
def get_progress(cwd: str) -> str:
    """Read the progress tracking file (.tasks/PROGRESS.md).

    Shows a table of all tasks with their status, duration, and result previews.
    The progress file is auto-updated whenever tasks start or finish.

    Args:
        cwd: Project root directory (same as used in run_task/start_task).
    """
    progress_path = Path(cwd) / ".tasks" / "PROGRESS.md"
    if not progress_path.exists():
        return json.dumps({
            "ok": True,
            "message": "No progress file yet. It will be created when the first task runs.",
        })

    content = progress_path.read_text(encoding="utf-8", errors="replace")
    return json.dumps({
        "ok": True,
        "progress": content,
    }, ensure_ascii=False)


# ── Profiles ─────────────────────────────────────────────────────────────────


@mcp.tool()
def list_profiles(cwd: Optional[str] = None) -> str:
    """List all available specialty profiles for teammates.

    Profiles define a teammate's role and expertise (e.g. "be-dev" for backend,
    "qa" for testing, "security" for security review). Use the profile name
    in run_task() or start_task() to assign a role to the teammate.

    Built-in profiles: be-dev, fe-dev, qa, ui-review, devops, security.
    Project-specific profiles can be added in <cwd>/.tasks/profiles/*.md.

    Args:
        cwd: Optional project root to also scan for project-level profiles.
    """
    profiles = profile_module.list_profiles(project_cwd=cwd)
    return json.dumps({
        "ok": True,
        "count": len(profiles),
        "profiles": [p.to_dict() for p in profiles],
    }, indent=2, ensure_ascii=False)


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    mcp.run()


if __name__ == "__main__":
    main()
