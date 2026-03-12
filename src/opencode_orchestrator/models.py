"""Data models for opencode-orchestrator v2."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    """Lifecycle states of a task."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    TIMEOUT = "timeout"


class IsolationMode(str, Enum):
    """Git isolation strategy for task execution."""
    NONE = "none"          # Work on current branch, no isolation
    BRANCH = "branch"      # Create a new branch (default)
    WORKTREE = "worktree"  # Create a git worktree (for parallel tasks)


@dataclass
class Task:
    """Represents a delegated coding task."""

    task_id: str
    spec: str                  # Full markdown spec content (or empty if using spec_file_path)
    cwd: str                   # Project root directory
    isolation: IsolationMode = IsolationMode.BRANCH
    model: Optional[str] = None
    agent: str = "opencode"
    visible: bool = True
    spec_file_path: Optional[str] = None  # Absolute path to pre-written spec file
    profile: Optional[str] = None         # Specialty profile name (e.g. "be-dev", "qa")

    # Runtime state
    status: TaskStatus = TaskStatus.PENDING
    effective_cwd: Optional[str] = None   # Actual cwd (may differ if worktree)
    branch: Optional[str] = None          # Git branch name
    result: Optional[str] = None          # Content of .result.md
    error: Optional[str] = None           # Error message if failed
    files_changed: list[str] = field(default_factory=list)

    # Timestamps
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    @property
    def duration_s(self) -> Optional[float]:
        """Task duration in seconds, or None if not completed."""
        if self.completed_at and self.started_at:
            return round(self.completed_at - self.started_at, 2)
        return None

    @property
    def spec_file(self) -> str:
        """Path to the spec file relative to cwd."""
        return f".tasks/{self.task_id}.md"

    @property
    def result_file(self) -> str:
        """Path to the result file relative to cwd."""
        return f".tasks/{self.task_id}.result.md"

    @property
    def log_file(self) -> str:
        """Path to the log file relative to cwd."""
        return f".tasks/{self.task_id}.log"

    @property
    def feedback_file(self) -> str:
        """Path to the feedback file relative to cwd."""
        return f".tasks/{self.task_id}.feedback.md"

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict for MCP responses."""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "isolation": self.isolation.value,
            "agent": self.agent,
            "model": self.model,
            "profile": self.profile,
            "visible": self.visible,
            "cwd": self.cwd,
            "effective_cwd": self.effective_cwd,
            "branch": self.branch,
            "spec_file": self.spec_file,
            "result_file": self.result_file,
            "result_preview": (
                (self.result[:300] + "...")
                if self.result and len(self.result) > 300
                else self.result
            ),
            "error": self.error,
            "files_changed": self.files_changed,
            "duration_s": self.duration_s,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
