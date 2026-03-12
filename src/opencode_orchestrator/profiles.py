"""Profile system for specialty roles (be-dev, fe-dev, qa, etc.).

Profiles are markdown files with YAML frontmatter that define a teammate's
specialty, instructions, and default configuration. When a profile is selected,
its instructions are injected into both the spec file and the agent prompt.

Profile search order:
  1. Project-level: <cwd>/.tasks/profiles/<name>.md
  2. Built-in:     <package>/../../profiles/<name>.md
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Profile dataclass ────────────────────────────────────────────────────────


@dataclass
class Profile:
    """A specialty role profile for a teammate agent."""

    name: str                          # ID, e.g. "be-dev"
    display_name: str = ""             # Human-friendly, e.g. "Backend Developer"
    description: str = ""              # One-liner description
    instructions: str = ""             # Full role instructions (markdown body)
    default_model: Optional[str] = None
    expertise: list[str] = field(default_factory=list)
    source: str = ""                   # File path this was loaded from

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "expertise": self.expertise,
            "default_model": self.default_model,
            "source": self.source,
        }


# ── Frontmatter parser (no pyyaml dependency) ───────────────────────────────


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse simple YAML frontmatter from markdown content.

    Returns:
        (frontmatter_dict, body_text)
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    raw = match.group(1)
    body = content[match.end():]
    meta: dict = {}

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()

        # Handle lists: [item1, item2]
        if val.startswith("[") and val.endswith("]"):
            items = [
                item.strip().strip("'\"")
                for item in val[1:-1].split(",")
                if item.strip()
            ]
            meta[key] = items
        # Handle null/none
        elif val.lower() in ("null", "none", "~", ""):
            meta[key] = None
        # Handle booleans
        elif val.lower() in ("true", "yes"):
            meta[key] = True
        elif val.lower() in ("false", "no"):
            meta[key] = False
        else:
            # Strip quotes
            meta[key] = val.strip("'\"")

    return meta, body


# ── Profile loading ──────────────────────────────────────────────────────────


def _load_profile_from_file(path: Path) -> Profile:
    """Load a single profile from a markdown file."""
    content = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(content)

    name = meta.get("name", path.stem)
    return Profile(
        name=name,
        display_name=meta.get("display_name", name),
        description=meta.get("description", ""),
        instructions=body.strip(),
        default_model=meta.get("default_model"),
        expertise=meta.get("expertise", []),
        source=str(path),
    )


def _get_builtin_profiles_dir() -> Path:
    """Get the built-in profiles directory (relative to package root)."""
    # profiles/ is at the repo root, alongside src/
    package_dir = Path(__file__).parent  # src/opencode_orchestrator/
    return package_dir.parent.parent / "profiles"


def _load_all_profiles(
    project_cwd: str | None = None,
) -> dict[str, Profile]:
    """Load all profiles from built-in and project-level directories.

    Project-level profiles override built-in profiles with the same name.
    """
    profiles: dict[str, Profile] = {}

    # 1. Built-in profiles
    builtin_dir = _get_builtin_profiles_dir()
    if builtin_dir.is_dir():
        for f in sorted(builtin_dir.glob("*.md")):
            try:
                p = _load_profile_from_file(f)
                profiles[p.name] = p
                logger.debug("Loaded built-in profile: %s from %s", p.name, f)
            except Exception as exc:
                logger.warning("Failed to load profile %s: %s", f, exc)

    # 2. Project-level profiles (override built-in)
    if project_cwd:
        project_dir = Path(project_cwd) / ".tasks" / "profiles"
        if project_dir.is_dir():
            for f in sorted(project_dir.glob("*.md")):
                try:
                    p = _load_profile_from_file(f)
                    profiles[p.name] = p
                    logger.debug("Loaded project profile: %s from %s", p.name, f)
                except Exception as exc:
                    logger.warning("Failed to load profile %s: %s", f, exc)

    return profiles


# ── Public API ───────────────────────────────────────────────────────────────


# Cache for built-in profiles (loaded once)
_builtin_cache: dict[str, Profile] | None = None


def get_profile(
    name: str,
    project_cwd: str | None = None,
) -> Profile:
    """Get a profile by name.

    Searches project-level first, then built-in.
    Raises ValueError if not found.
    """
    global _builtin_cache

    # Check project-level first
    if project_cwd:
        project_dir = Path(project_cwd) / ".tasks" / "profiles"
        project_file = project_dir / f"{name}.md"
        if project_file.exists():
            return _load_profile_from_file(project_file)

    # Check built-in
    if _builtin_cache is None:
        _builtin_cache = _load_all_profiles()

    if name in _builtin_cache:
        return _builtin_cache[name]

    available = ", ".join(sorted(_builtin_cache.keys()))
    raise ValueError(
        f"Unknown profile '{name}'. Available profiles: {available}"
    )


def list_profiles(
    project_cwd: str | None = None,
) -> list[Profile]:
    """List all available profiles (built-in + project-level)."""
    profiles = _load_all_profiles(project_cwd)
    return sorted(profiles.values(), key=lambda p: p.name)


def reload_profiles() -> None:
    """Clear the profile cache — forces re-loading on next access."""
    global _builtin_cache
    _builtin_cache = None
