"""OpenCode agent plugin.

OpenCode (https://github.com/opencode-ai/opencode) is a TUI-based coding
agent that supports multiple LLM providers (Kimi, GLM, GPT-4o, Qwen, etc.).
"""

from __future__ import annotations

import os

from .base import AgentPlugin


class OpenCodeAgent(AgentPlugin):
    """OpenCode coding agent backend."""

    name = "opencode"

    def build_command(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
    ) -> list[str]:
        """Build opencode run command.

        Usage: opencode run [--dir DIR] [-m model] <message..>
        """
        cmd = ["opencode", "run", "--dir", cwd]
        if model:
            cmd.extend(["-m", model])
        cmd.append(prompt)
        return cmd

    def build_env(self, model: str | None = None) -> dict[str, str]:
        """Build env with optional OPENCODE_MODEL override."""
        env = os.environ.copy()
        if model:
            env["OPENCODE_MODEL"] = model
        return env

    @property
    def supports_prompt_file(self) -> bool:
        """OpenCode does not support --prompt-file natively."""
        return False
