# Pushing opencode-orchestrator to GitHub

Due to sandbox filesystem limitations, the git commit couldn't be completed in the cloud environment. Here are the steps to finalize the push on your local machine:

## Quick Setup (copy & paste)

```bash
# Navigate to your project directory
cd /path/to/opencode-orchestrator

# Initialize git (if not already done)
git init

# Configure git
git config user.email "your-email@example.com"
git config user.name "Your Name"

# Add all files
git add .

# Create initial commit
git commit -m "Initial commit: opencode-orchestrator v2 with profile system

Features:
- File-based communication (spec files, result files, progress tracking)
- 1 tmux session, N windows for agent monitoring
- Plugin architecture (agents, profiles)
- Git isolation modes (none, branch, worktree)
- 6 built-in profiles: be-dev, fe-dev, qa, ui-review, devops, security
- 9 MCP tools for team lead delegation

Profile system allows team lead to assign specialty roles to teammates,
with role-specific instructions injected into spec files and prompts."

# Add GitHub remote
git remote add origin https://github.com/ntlongctt/opencode_orchestrator.git

# Create main branch and push (if repo was just created)
git branch -M main
git push -u origin main

# Or just push if repo was already set up
git push
```

## What's included in this repository

### Core Files
- **src/opencode_orchestrator/**
  - `profiles.py` — Profile loader and registry
  - `server.py` — 9 MCP tools for delegation
  - `task_manager.py` — Task execution and orchestration
  - `models.py` — Data models (Task, TaskStatus, IsolationMode)
  - `tmux.py` — Tmux session/window management
  - `agents/` — Plugin system (base.py, opencode.py)

### Profiles
- **profiles/** — 6 built-in specialty profiles
  - `be-dev.md` — Backend Developer
  - `fe-dev.md` — Frontend Developer
  - `qa.md` — QA Engineer
  - `ui-review.md` — UI/UX Reviewer
  - `devops.md` — DevOps Engineer
  - `security.md` — Security Reviewer

### Documentation
- `SKILL.md` — Claude Code skill documentation
- `README.md` — Project overview and usage guide
- `ARCHITECTURE-V2.md` — Design and architecture documentation

## What to do next

1. **Update .gitignore** — Already included to exclude `__pycache__/`, `.DS_Store`, `.tasks/`, etc.
2. **Create a README in GitHub** — Use the `README.md` included in the repo
3. **Set repository description** — "MCP server for delegating coding tasks to AI agents with specialty profiles"
4. **Add topics** — mcp, ai-agents, opencode, delegation, profiles

## Troubleshooting

If you get "fatal: not a git repository" when trying to push:
```bash
git init
git add .
git commit -m "Initial commit..."
```

If you get "Your branch is ahead of 'origin/main'":
```bash
# First push
git push -u origin main

# Subsequent pushes
git push
```
