"""Base class for agent plugins.

Each coding agent backend (OpenCode, Aider, Codex, etc.) implements this
interface so the orchestrator can swap agents without changing core logic.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


class AgentPlugin(ABC):
    """Interface that every coding agent must implement."""

    name: str  # e.g. "opencode", "aider"

    @abstractmethod
    def build_command(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
    ) -> list[str]:
        """Build the CLI command to run this agent.

        Args:
            cwd:    Working directory for the agent.
            prompt: Short prompt (1-2 sentences) pointing to the spec file.
                    e.g. "Read .tasks/task-auth.md and complete the work."
            model:  Optional model override.

        Returns:
            Command list suitable for subprocess / tmux execution.
        """
        ...

    @abstractmethod
    def build_env(self, model: str | None = None) -> dict[str, str]:
        """Build environment variables for the agent subprocess.

        Args:
            model: Optional model override.

        Returns:
            Environment dict (copy of os.environ + agent-specific vars).
        """
        ...

    @property
    @abstractmethod
    def supports_prompt_file(self) -> bool:
        """Whether the agent natively supports reading prompt from a file.

        If True, task_manager can use --prompt-file / --message-file
        instead of passing prompt as a CLI argument.
        """
        ...

    def build_shell_command(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
    ) -> str:
        """Build a shell-escaped command string for tmux execution.

        Default implementation joins build_command() with proper quoting.
        Override if the agent needs special shell handling.
        """
        import shlex
        parts = self.build_command(cwd=cwd, prompt=prompt, model=model)
        return " ".join(shlex.quote(p) for p in parts)
