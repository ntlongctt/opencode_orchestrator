"""Agent plugins for opencode-orchestrator.

Registry of available coding agent backends.
"""

from __future__ import annotations

from .base import AgentPlugin
from .opencode import OpenCodeAgent

# Agent registry — add new agents here
_REGISTRY: dict[str, type[AgentPlugin]] = {
    "opencode": OpenCodeAgent,
}


def get_agent(name: str) -> AgentPlugin:
    """Get an agent plugin instance by name.

    Raises:
        ValueError: If agent name is not registered.
    """
    cls = _REGISTRY.get(name)
    if cls is None:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise ValueError(
            f"Unknown agent '{name}'. Available agents: {available}"
        )
    return cls()


def list_agents() -> list[str]:
    """Return names of all registered agent plugins."""
    return sorted(_REGISTRY.keys())


__all__ = ["AgentPlugin", "get_agent", "list_agents"]
