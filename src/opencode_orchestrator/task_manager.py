"""Core task management: spec files, tmux execution, result collection.

This replaces v1's PTY-based approach with file-based communication:
  - Leader writes .tasks/<id>.md        (spec)
  - Teammate reads spec, does the work
  - Teammate writes .tasks/<id>.result.md  (report)
  - Orchestrator reads result file
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from . import tmux
from .agents import get_agent
from .models import IsolationMode, Task, TaskStatus
from .profiles import get_profile

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 600  # seconds


# ── Spec file generation ─────────────────────────────────────────────────────


_WHEN_DONE_TEMPLATE = """
## When Done

**MANDATORY:** Write your completion report to `{result_file}` with this format:

```markdown
# Result: {task_id}

## Status: DONE | PARTIAL | BLOCKED

## Summary
2-3 sentences describing what you did.

## Files Changed
- path/to/file.ts (created — N lines)
- path/to/other.ts (modified — description)

## Test Results
✅ N tests passed, M failed
(paste relevant test output)

## Issues
Any problems encountered, or "None".

## Git
Commit all changes on your branch with a descriptive message.
```

**IMPORTANT:** The result file MUST exist when you are done. Without it,
the orchestrator cannot confirm your work was completed.
"""


def _build_role_context(task: Task) -> str:
    """Build the role context section from the task's profile.

    Returns:
        Markdown string to prepend to spec, or empty string if no profile.
    """
    if not task.profile:
        return ""

    try:
        profile = get_profile(task.profile, project_cwd=task.cwd)
    except ValueError as exc:
        logger.warning("Profile lookup failed: %s", exc)
        return ""

    lines = [
        f"## Role: {profile.display_name}",
        "",
        f"_{profile.description}_",
        "",
        profile.instructions,
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def _resolve_spec(task: Task) -> str:
    """Resolve the spec content and ensure the spec file exists.

    If task.spec_file_path is set → read from that file (leader already wrote it).
    Otherwise → write task.spec to .tasks/<task_id>.md.

    In both cases:
    - Prepends profile role context if profile is set
    - Appends "When Done" section if missing

    Returns:
        Absolute path to the spec file.
    """
    tasks_dir = Path(task.cwd) / ".tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    spec_path = tasks_dir / f"{task.task_id}.md"

    # Get the raw spec content
    if task.spec_file_path:
        src = Path(task.spec_file_path)
        if not src.exists():
            raise FileNotFoundError(
                f"spec_file not found: {task.spec_file_path}"
            )
        raw_spec = src.read_text(encoding="utf-8").rstrip()
        logger.info("Using pre-written spec file: %s", src)
    else:
        raw_spec = task.spec.rstrip()

    # Prepend profile role context (if profile set and not already in spec)
    role_context = _build_role_context(task)
    if role_context and "## Role:" not in raw_spec:
        full_spec = role_context + raw_spec
    else:
        full_spec = raw_spec

    # Append "When Done" section if missing
    if "## When Done" not in full_spec and "## when done" not in full_spec.lower():
        full_spec += "\n\n" + _WHEN_DONE_TEMPLATE.format(
            task_id=task.task_id,
            result_file=task.result_file,
        )

    # Write to canonical path
    spec_path.write_text(full_spec, encoding="utf-8")
    logger.info("Wrote spec file: %s", spec_path)

    # Populate task.spec for consistency
    task.spec = full_spec

    return str(spec_path)


# ── Git isolation ────────────────────────────────────────────────────────────


def _setup_isolation(task: Task) -> str:
    """Setup git isolation and return the effective cwd.

    Returns:
        The working directory the agent should use.
    """
    branch_name = f"task/{task.task_id}"
    task.branch = branch_name

    if task.isolation == IsolationMode.NONE:
        task.branch = None
        return task.cwd

    elif task.isolation == IsolationMode.BRANCH:
        # Create branch (agent will checkout in spec instructions)
        result = subprocess.run(
            ["git", "branch", branch_name],
            cwd=task.cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and "already exists" not in result.stderr:
            logger.warning("Failed to create branch %s: %s",
                           branch_name, result.stderr.strip())
        return task.cwd

    elif task.isolation == IsolationMode.WORKTREE:
        worktree_path = f"/tmp/oc-worktree-{task.task_id}"
        # Clean up stale worktree if exists
        if Path(worktree_path).exists():
            subprocess.run(
                ["git", "worktree", "remove", worktree_path, "--force"],
                cwd=task.cwd, capture_output=True,
            )
        result = subprocess.run(
            ["git", "worktree", "add", worktree_path, "-b", branch_name],
            cwd=task.cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # Branch might already exist — try without -b
            result = subprocess.run(
                ["git", "worktree", "add", worktree_path, branch_name],
                cwd=task.cwd,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to create worktree: {result.stderr.strip()}"
                )
        logger.info("Created worktree at %s on branch %s",
                     worktree_path, branch_name)
        return worktree_path

    return task.cwd


def _cleanup_isolation(task: Task) -> None:
    """Cleanup git isolation artifacts after task completion."""
    if task.isolation == IsolationMode.WORKTREE:
        worktree_path = f"/tmp/oc-worktree-{task.task_id}"
        if Path(worktree_path).exists():
            subprocess.run(
                ["git", "worktree", "remove", worktree_path, "--force"],
                cwd=task.cwd,
                capture_output=True,
            )
            logger.info("Removed worktree: %s", worktree_path)


# ── Result collection ────────────────────────────────────────────────────────


def _collect_result(task: Task) -> dict:
    """Read the result file (or fallback to log) after task completion.

    Returns:
        Dict with: status, result, log_tail, files_changed, warning, error
    """
    cwd = task.effective_cwd or task.cwd
    result_path = Path(cwd) / ".tasks" / f"{task.task_id}.result.md"
    log_path = Path(cwd) / ".tasks" / f"{task.task_id}.log"

    out: dict = {}

    # Read result file
    if result_path.exists():
        content = result_path.read_text(encoding="utf-8", errors="replace")
        out["result"] = content

        # Parse status line
        for line in content.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("## status:"):
                status_str = stripped.split(":", 1)[1].strip()
                if "done" in status_str:
                    out["status"] = "done"
                elif "partial" in status_str:
                    out["status"] = "partial"
                elif "blocked" in status_str:
                    out["status"] = "blocked"
                else:
                    out["status"] = status_str
                break
        else:
            out["status"] = "done"  # result file exists → assume done
    else:
        out["result"] = None
        out["status"] = "unknown"
        out["warning"] = "No result file written. Check log and working directory."

    # Read log tail as fallback info
    if log_path.exists():
        try:
            log_content = log_path.read_text(
                encoding="utf-8", errors="replace"
            )
            out["log_tail"] = log_content[-2000:]
        except Exception:
            pass

    # Detect changed files via git
    try:
        branch = task.branch
        if branch:
            diff_result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD", branch],
                cwd=task.cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if diff_result.returncode == 0 and diff_result.stdout.strip():
                out["files_changed"] = diff_result.stdout.strip().split("\n")
    except Exception:
        pass

    return out


# ── Progress tracking ────────────────────────────────────────────────────────


_PROGRESS_FILENAME = "PROGRESS.md"


def _update_progress_file(cwd: str, tasks: dict[str, "Task"]) -> str:
    """Write/update .tasks/PROGRESS.md with current status of all tasks.

    Returns:
        Absolute path to the progress file.
    """
    tasks_dir = Path(cwd) / ".tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    progress_path = tasks_dir / _PROGRESS_FILENAME

    lines = [
        "# Task Progress",
        "",
        f"_Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
        "| # | Task ID | Status | Duration | Branch |",
        "|---|---------|--------|----------|--------|",
    ]

    status_icons = {
        "pending": "⏳",
        "running": "🔄",
        "done": "✅",
        "failed": "❌",
        "timeout": "⏱️",
    }

    for i, (tid, task) in enumerate(sorted(tasks.items()), 1):
        icon = status_icons.get(task.status.value, "❓")
        dur = f"{task.duration_s:.1f}s" if task.duration_s else "—"
        branch = task.branch or "—"
        lines.append(
            f"| {i} | `{tid}` | {icon} {task.status.value} | {dur} | {branch} |"
        )

    # Append summary of completed results
    done_tasks = [t for t in tasks.values() if t.result]
    if done_tasks:
        lines.extend(["", "---", "", "## Results Summary", ""])
        for task in done_tasks:
            # Extract first 3 lines of result (after header)
            result_lines = [
                l for l in (task.result or "").splitlines()
                if l.strip() and not l.startswith("# Result:")
            ][:3]
            preview = " ".join(result_lines)[:200]
            lines.append(f"### `{task.task_id}`")
            lines.append(f"{preview}")
            lines.append("")

    progress_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Updated progress file: %s", progress_path)
    return str(progress_path)


# ── Subprocess fallback (no tmux) ────────────────────────────────────────────


def _run_subprocess_fallback(
    cmd: list[str],
    cwd: str,
    log_file: str,
    env: dict,
    timeout: int,
) -> bool:
    """Run agent as a plain subprocess when tmux is not available.

    Communication is file-based, so this works fine — just no monitoring.

    Returns:
        True if process completed, False if timeout.
    """
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "w") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            return False


# ── Task Manager ─────────────────────────────────────────────────────────────


class TaskManager:
    """Manages the lifecycle of delegated coding tasks."""

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────

    def run_sync(
        self,
        task_id: str,
        spec: str = "",
        cwd: str = "",
        isolation: str = "branch",
        model: str | None = None,
        agent: str = "opencode",
        timeout: int = DEFAULT_TIMEOUT,
        visible: bool = True,
        spec_file: str | None = None,
        profile: str | None = None,
    ) -> dict:
        """Run a task synchronously (blocking).

        This is the primary entry point — blocks until the agent completes.
        Either `spec` (inline string) or `spec_file` (path to .md) must be provided.
        """
        task = self._create_task(
            task_id=task_id, spec=spec, cwd=cwd,
            isolation=isolation, model=model, agent=agent, visible=visible,
            spec_file=spec_file, profile=profile,
        )
        self._execute_task(task, timeout=timeout)
        return self._build_response(task)

    def start_async(
        self,
        task_id: str,
        spec: str = "",
        cwd: str = "",
        isolation: str = "branch",
        model: str | None = None,
        agent: str = "opencode",
        visible: bool = True,
        spec_file: str | None = None,
        profile: str | None = None,
    ) -> dict:
        """Start a task asynchronously (non-blocking).

        Returns immediately. Use wait_tasks() to block until completion.
        Either `spec` (inline string) or `spec_file` (path to .md) must be provided.
        """
        task = self._create_task(
            task_id=task_id, spec=spec, cwd=cwd,
            isolation=isolation, model=model, agent=agent, visible=visible,
            spec_file=spec_file, profile=profile,
        )
        t = threading.Thread(
            target=self._execute_task,
            args=(task,),
            kwargs={"timeout": DEFAULT_TIMEOUT},
            daemon=True,
            name=f"oc-task-{task_id}",
        )
        t.start()
        return {
            "ok": True,
            "message": f"Task '{task_id}' started asynchronously.",
            "task": task.to_dict(),
        }

    def wait_tasks(
        self,
        task_ids: list[str],
        timeout: int = DEFAULT_TIMEOUT,
    ) -> dict:
        """Block until all specified tasks complete (or timeout)."""
        deadline = time.time() + timeout
        pending = set(task_ids)
        results: dict[str, str] = {}

        while pending and time.time() < deadline:
            for tid in list(pending):
                task = self._tasks.get(tid)
                if not task:
                    results[tid] = "not_found"
                    pending.discard(tid)
                elif task.status in (
                    TaskStatus.DONE,
                    TaskStatus.FAILED,
                    TaskStatus.TIMEOUT,
                ):
                    results[tid] = task.status.value
                    pending.discard(tid)
            if pending:
                time.sleep(2)

        for tid in pending:
            results[tid] = "timeout"

        return {
            "ok": True,
            "results": results,
            "all_done": all(v == "done" for v in results.values()),
            "any_failed": any(v in ("failed", "timeout") for v in results.values()),
        }

    def get_result(self, task_id: str) -> dict:
        """Get the result of a task."""
        task = self._tasks.get(task_id)
        if not task:
            return {"ok": False, "error": f"Task '{task_id}' not found."}

        if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return {
                "ok": True,
                "task_id": task_id,
                "status": task.status.value,
                "message": "Task is still running.",
            }

        return {
            "ok": True,
            "task_id": task_id,
            "status": task.status.value,
            "result": task.result,
            "error": task.error,
            "files_changed": task.files_changed,
            "duration_s": task.duration_s,
        }

    def peek(self, task_id: str) -> dict:
        """Capture the current tmux pane content for a running task."""
        task = self._tasks.get(task_id)
        if not task:
            return {"ok": False, "error": f"Task '{task_id}' not found."}

        if not task.visible:
            # Headless — read log file instead
            cwd = task.effective_cwd or task.cwd
            log_path = Path(cwd) / ".tasks" / f"{task_id}.log"
            if log_path.exists():
                content = log_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                return {"ok": True, "source": "log", "content": content[-3000:]}
            return {"ok": False, "error": "No log file found (headless mode)."}

        if not tmux.tmux_available():
            return {"ok": False, "error": "tmux not available."}

        content = tmux.capture_pane(task_id)
        return {"ok": True, "source": "tmux", "content": content}

    def do_send_feedback(self, task_id: str, feedback: str) -> dict:
        """Send feedback to a running (or completed) task."""
        task = self._tasks.get(task_id)
        if not task:
            return {"ok": False, "error": f"Task '{task_id}' not found."}

        cwd = task.effective_cwd or task.cwd
        feedback_path = Path(cwd) / ".tasks" / f"{task_id}.feedback.md"
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_path.write_text(
            f"# Feedback: {task_id}\n\n{feedback}\n",
            encoding="utf-8",
        )
        logger.info("Wrote feedback to %s", feedback_path)

        # If task is still running in tmux, notify the agent
        if (
            task.status == TaskStatus.RUNNING
            and task.visible
            and tmux.tmux_available()
            and tmux.window_exists(task_id)
        ):
            try:
                tmux.send_keys(
                    task_id,
                    f"# FEEDBACK: Check {task.feedback_file} for review comments",
                )
                return {
                    "ok": True,
                    "message": f"Feedback written and agent notified via tmux.",
                }
            except Exception:
                pass

        return {
            "ok": True,
            "message": f"Feedback written to {task.feedback_file}. "
                       "Agent is not running — re-run task to apply feedback.",
        }

    def list_all(self, status_filter: str | None = None) -> list[dict]:
        """List all tasks, optionally filtered by status."""
        tasks = list(self._tasks.values())
        if status_filter:
            tasks = [t for t in tasks if t.status.value == status_filter]
        return [t.to_dict() for t in tasks]

    # ── Internal ──────────────────────────────────────────────────────────

    def _create_task(
        self,
        task_id: str,
        spec: str,
        cwd: str,
        isolation: str,
        model: str | None,
        agent: str,
        visible: bool,
        spec_file: str | None = None,
        profile: str | None = None,
    ) -> Task:
        """Create and register a new task."""
        with self._lock:
            if task_id in self._tasks:
                existing = self._tasks[task_id]
                if existing.status == TaskStatus.RUNNING:
                    raise ValueError(
                        f"Task '{task_id}' is already running."
                    )
                # Allow re-running completed tasks
                logger.info("Re-creating task '%s' (was %s)",
                            task_id, existing.status.value)

            try:
                iso_mode = IsolationMode(isolation)
            except ValueError:
                iso_mode = IsolationMode.BRANCH

            # If profile has default_model and no explicit model, use it
            effective_model = model
            if profile and not model:
                try:
                    prof = get_profile(profile, project_cwd=cwd)
                    if prof.default_model:
                        effective_model = prof.default_model
                        logger.info(
                            "Using profile '%s' default model: %s",
                            profile, effective_model,
                        )
                except ValueError:
                    pass  # Profile not found — will fail later in _resolve_spec

            task = Task(
                task_id=task_id,
                spec=spec,
                cwd=os.path.abspath(cwd),
                isolation=iso_mode,
                model=effective_model,
                agent=agent,
                visible=visible,
                spec_file_path=spec_file,
                profile=profile,
            )
            self._tasks[task_id] = task
            return task

    def _execute_task(self, task: Task, timeout: int = DEFAULT_TIMEOUT) -> None:
        """Execute a task: write spec, setup isolation, run agent, collect result."""
        try:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()

            # 1. Setup isolation (git branch/worktree)
            task.effective_cwd = _setup_isolation(task)

            # 2. Resolve spec file (write or read from pre-written)
            spec_path = _resolve_spec(task)

            # 2b. Update progress file
            _update_progress_file(task.cwd, self._tasks)

            # 3. Build agent command
            agent_plugin = get_agent(task.agent)

            # Build prompt — inject profile role into prompt command too
            role_prefix = ""
            if task.profile:
                try:
                    profile = get_profile(task.profile, project_cwd=task.cwd)
                    role_prefix = (
                        f"[ROLE: {profile.display_name}] "
                        f"{profile.description}. "
                    )
                except ValueError:
                    pass  # Profile already injected into spec, prompt is best-effort

            prompt = (
                f"{role_prefix}"
                f"Read the task specification at {task.spec_file} "
                f"and complete all work described there. "
                f"When finished, write your report to {task.result_file} "
                f"following the template in the spec."
            )
            cmd = agent_plugin.build_command(
                cwd=task.effective_cwd,
                prompt=prompt,
                model=task.model,
            )
            env = agent_plugin.build_env(model=task.model)
            shell_cmd = agent_plugin.build_shell_command(
                cwd=task.effective_cwd,
                prompt=prompt,
                model=task.model,
            )

            # 4. Execute
            log_path = str(
                Path(task.effective_cwd) / ".tasks" / f"{task.task_id}.log"
            )
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)

            timed_out = False

            if task.visible and tmux.tmux_available():
                # Run in tmux window
                tmux.create_task_window(
                    task_id=task.task_id,
                    command=shell_cmd,
                    cwd=task.effective_cwd,
                    log_file=log_path,
                )
                completed = tmux.wait_window_exit(
                    task.task_id, timeout=timeout
                )
                if not completed:
                    timed_out = True
                    tmux.kill_window(task.task_id)
            else:
                # Fallback: plain subprocess
                completed = _run_subprocess_fallback(
                    cmd=cmd,
                    cwd=task.effective_cwd,
                    log_file=log_path,
                    env=env,
                    timeout=timeout,
                )
                if not completed:
                    timed_out = True

            # 5. Collect result
            task.completed_at = time.time()
            collected = _collect_result(task)

            task.result = collected.get("result")
            task.files_changed = collected.get("files_changed", [])

            if timed_out:
                task.status = TaskStatus.TIMEOUT
                task.error = f"Task timed out after {timeout}s."
                if task.result:
                    task.error += " Result file was found (work may be complete)."
            elif collected.get("status") in ("done", "partial"):
                task.status = TaskStatus.DONE
            elif collected.get("status") == "blocked":
                task.status = TaskStatus.FAILED
                task.error = "Agent reported BLOCKED status."
            elif task.result is None:
                task.status = TaskStatus.FAILED
                task.error = collected.get(
                    "warning",
                    "No result file written."
                )
            else:
                task.status = TaskStatus.DONE

            # 6. Update progress file with final status
            _update_progress_file(task.cwd, self._tasks)

            # 7. Cleanup worktree (but keep branch)
            _cleanup_isolation(task)

            # 8. Cleanup tmux session if empty
            if tmux.tmux_available():
                tmux.cleanup_session()

        except Exception as exc:
            logger.exception("Task %s failed with exception", task.task_id)
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            task.completed_at = time.time()

    def _build_response(self, task: Task) -> dict:
        """Build the JSON response for a completed task."""
        return {
            "ok": task.status in (TaskStatus.DONE, TaskStatus.TIMEOUT),
            "task_id": task.task_id,
            "status": task.status.value,
            "result": task.result,
            "error": task.error,
            "files_changed": task.files_changed,
            "branch": task.branch,
            "duration_s": task.duration_s,
            "spec_file": task.spec_file,
            "result_file": task.result_file,
        }
