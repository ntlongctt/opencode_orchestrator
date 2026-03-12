"""Tmux session management for opencode-orchestrator.

Architecture: 1 session ("oc-tasks"), N windows (one per task).
Each task runs as a named window inside the shared session.

User experience:
    tmux attach -t oc-tasks      # enter the "control room"
    Ctrl-b w                     # list all task windows
    Ctrl-b n / p                 # switch between tasks
    Ctrl-b d                     # detach back to terminal

When a task's command exits, its tmux window automatically disappears.
This is how the orchestrator knows a task has completed.
"""

from __future__ import annotations

import logging
import platform
import shlex
import shutil
import subprocess
import time

logger = logging.getLogger(__name__)

SESSION_NAME = "oc-tasks"


# ── Availability ──────────────────────────────────────────────────────────────


def tmux_available() -> bool:
    """Check if tmux is installed and accessible."""
    return shutil.which("tmux") is not None


# ── Session helpers (internal) ────────────────────────────────────────────────


def _session_exists() -> bool:
    """Check if the oc-tasks session is alive."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", SESSION_NAME],
        capture_output=True,
    )
    return result.returncode == 0


def _wrap_command(command: str, log_file: str | None) -> str:
    """Wrap command to capture terminal output to a log file.

    Uses `script` (available on macOS and Linux) to record the full
    terminal session including TUI rendering.
    """
    if not log_file:
        return command

    system = platform.system()
    escaped_cmd = command.replace("'", "'\\''")

    if system == "Darwin":
        # macOS: script -q -F <file> <command>
        return f"script -q -F {shlex.quote(log_file)} bash -c '{escaped_cmd}'"
    else:
        # Linux: script -q -f <file> -c <command>
        return f"script -q -f {shlex.quote(log_file)} -c '{escaped_cmd}'"


# ── Window management (public API) ───────────────────────────────────────────


def create_task_window(
    task_id: str,
    command: str,
    cwd: str,
    log_file: str | None = None,
) -> None:
    """Create a new tmux window for a task inside the oc-tasks session.

    If the session doesn't exist yet, creates it with the first window.
    If it already exists, adds a new window.

    Args:
        task_id:  Unique task identifier — becomes the window name.
        command:  Shell command string to run in the window.
        cwd:     Working directory for the command.
        log_file: Optional path to capture terminal output via `script`.
    """
    wrapped = _wrap_command(command, log_file)

    if not _session_exists():
        subprocess.run(
            [
                "tmux", "new-session",
                "-d",               # detached
                "-s", SESSION_NAME,  # session name
                "-n", task_id,       # window name
                "-c", cwd,           # working directory
                "bash", "-c", wrapped,
            ],
            check=True,
        )
        logger.info(
            "Created session '%s' with window '%s'", SESSION_NAME, task_id
        )
    else:
        subprocess.run(
            [
                "tmux", "new-window",
                "-t", SESSION_NAME,  # target session
                "-n", task_id,       # window name
                "-c", cwd,           # working directory
                "bash", "-c", wrapped,
            ],
            check=True,
        )
        logger.info(
            "Added window '%s' to session '%s'", task_id, SESSION_NAME
        )


def window_exists(task_id: str) -> bool:
    """Check if a task's window is still alive.

    Windows automatically disappear when their command exits.
    This is the primary signal that a task has completed.
    """
    result = subprocess.run(
        ["tmux", "list-windows", "-t", SESSION_NAME, "-F", "#{window_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False  # session doesn't exist
    windows = result.stdout.strip().split("\n")
    return task_id in windows


def wait_window_exit(
    task_id: str,
    timeout: int = 600,
    poll_interval: float = 2.0,
) -> bool:
    """Block until a task's tmux window exits (= task completed).

    Args:
        task_id:       The task window to wait for.
        timeout:       Max seconds to wait before giving up.
        poll_interval: Seconds between polls.

    Returns:
        True if the window exited normally, False if timeout was reached.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not window_exists(task_id):
            return True
        time.sleep(poll_interval)
    return False


def capture_pane(task_id: str, lines: int = 100) -> str:
    """Capture the current visible content of a task's window.

    Used by peek_task to see what an agent is currently doing
    without needing to tmux-attach.

    Args:
        task_id: The task window to capture.
        lines:   Number of lines to capture from scroll-back.

    Returns:
        The captured text content, or empty string if window not found.
    """
    target = f"{SESSION_NAME}:{task_id}"
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", target, "-p", "-S", f"-{lines}"],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def send_keys(task_id: str, text: str) -> None:
    """Send keystrokes to a task's tmux window.

    Used by send_feedback to inject text into a running agent.
    Note: this sends keystrokes, not stdin.

    Args:
        task_id: The task window to send keys to.
        text:    Text to type (Enter is sent automatically).
    """
    target = f"{SESSION_NAME}:{task_id}"
    subprocess.run(
        ["tmux", "send-keys", "-t", target, text, "Enter"],
        check=True,
    )


def kill_window(task_id: str) -> None:
    """Kill a single task's window without affecting other tasks."""
    target = f"{SESSION_NAME}:{task_id}"
    subprocess.run(
        ["tmux", "kill-window", "-t", target],
        capture_output=True,
    )


def list_windows() -> list[str]:
    """List all active task windows (= currently running tasks)."""
    result = subprocess.run(
        ["tmux", "list-windows", "-t", SESSION_NAME, "-F", "#{window_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [w for w in result.stdout.strip().split("\n") if w]


def cleanup_session() -> None:
    """Remove the oc-tasks session if no windows remain.

    Call after all tasks have completed to clean up.
    """
    if _session_exists() and not list_windows():
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME],
            capture_output=True,
        )
        logger.info("Cleaned up empty session '%s'", SESSION_NAME)
