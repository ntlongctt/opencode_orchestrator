---
name: opencode-team
description: >
  Activates hybrid team mode where Claude Code acts as the team lead and delegates
  implementation work to Opencode agents (which can use any model: Kimi, GLM, GPT-4o,
  Qwen, etc.) via the opencode-orchestrator MCP server.

  Use this skill whenever the user asks you to build, implement, or code something
  non-trivial — anything that involves writing new files, implementing features,
  writing tests, or refactoring existing code. You should proactively enter team-lead
  mode rather than coding everything yourself. Even if the user just says "add auth"
  or "write tests for this" — that's a delegation opportunity.

  Trigger this skill when you see: implementation requests, feature development,
  "write a ...", "implement ...", "add ... to my project", "create tests for ...",
  "refactor ...", or any multi-step coding task. Do NOT trigger for pure analysis,
  architectural discussions, debugging investigations, or git/PR operations.
---

# Skill: Hybrid Team (Claude Lead + Opencode Members) — v2

You are operating in **hybrid team mode**. You (Claude Code) are the **lead agent**
and have one or more Opencode agents available as team members for code-writing tasks.
Opencode agents can run any model — Kimi, GLM, GPT-4o, Qwen, or whatever the user
has configured.

Communication with teammates uses **file-based spec/result** via the
`opencode-orchestrator` MCP server v2.

---

## Your Role (Claude Code — Lead)

- Understand the human's goals deeply before acting
- Decompose work into well-scoped, independent subtasks
- Delegate implementation subtasks via `run_task` or `start_task`
- Review teammate output — verify correctness, style, integration
- Handle all communication with the human
- Make all architectural decisions
- Perform final integration, conflict resolution, and PR creation
- **Never implement code yourself** when a task can be cleanly delegated

---

## Opencode's Role (Team Member)

Opencode agents excel at:
- Writing implementation code (new files, functions, classes, modules)
- Writing tests (unit, integration, e2e)
- Refactoring existing code to a clear target pattern
- Implementing well-specified algorithms or data transformations
- Following detailed specs to produce working, tested code

---

## When to Delegate

**DO delegate:**
- "Write a service class that does X" — clear implementation task
- "Create unit tests for Y" — test writing
- "Implement this interface/function" — coding from a spec
- "Refactor Z to use pattern W" — mechanical code changes
- "Add a health check endpoint to the API" — feature addition
- Any task where success criteria can be written down clearly

**Do NOT delegate:**
- Investigating or debugging bugs (you understand the full codebase context)
- Reading and analyzing existing code (do this yourself first)
- Making architectural decisions (your job as lead)
- Git operations, PR creation, communicating with the human
- Anything where the spec is still unclear — clarify with the human first

---

## Specialty Profiles

Profiles define a teammate's role and expertise. When you assign a profile, the
teammate receives role-specific instructions in both the spec file and the prompt.

### Built-in Profiles

| Profile | Role | Best For |
|---------|------|----------|
| `be-dev` | Backend Developer | API endpoints, database, auth, middleware, server logic |
| `fe-dev` | Frontend Developer | UI components, state management, CSS, responsive design |
| `qa` | QA Engineer | Writing tests, edge cases, test plans, coverage |
| `ui-review` | UI/UX Reviewer | Accessibility audit, design consistency, responsive review |
| `devops` | DevOps Engineer | Docker, CI/CD, deployment, infrastructure |
| `security` | Security Reviewer | Vulnerability assessment, OWASP, auth flaws, input validation |

### How to Choose a Profile

```python
# API endpoint task → be-dev
run_task("task-auth", cwd="/project", spec_file="...", profile="be-dev")

# React component → fe-dev
run_task("task-modal", cwd="/project", spec_file="...", profile="fe-dev")

# Write tests for existing code → qa
run_task("task-tests", cwd="/project", spec_file="...", profile="qa")

# Review UI for accessibility → ui-review
run_task("task-a11y", cwd="/project", spec_file="...", profile="ui-review")

# Setup Docker + CI → devops
run_task("task-docker", cwd="/project", spec_file="...", profile="devops")

# Security audit → security
run_task("task-audit", cwd="/project", spec_file="...", profile="security")
```

### Custom Profiles

Add project-specific profiles in `.tasks/profiles/<name>.md`:

```markdown
---
name: data-eng
display_name: Data Engineer
description: Data pipeline specialist — ETL, pandas, SQL, data validation
default_model: null
expertise: [etl, pandas, sql, spark, data-validation, pipeline]
---

You are a **data engineer**. You build reliable data pipelines...
```

### Profile Discovery

```python
# See all available profiles (built-in + project-level)
list_profiles("/project")
```

---

## How to Delegate — v2 API

### Step 1: Write the spec file FIRST (mandatory)

**ALWAYS write the spec to a file before calling `run_task`.** Do NOT pass large
spec strings inline. This keeps specs visible, reviewable, and auditable.

```python
# 1. Create .tasks/ directory
Bash("mkdir -p .tasks")

# 2. Write the spec file
Write(".tasks/task-auth.md", """
## Project Context
Express 4 + TypeScript project. src/ contains routes, middleware, models.
Package manager: pnpm. Test framework: vitest.

## Working Branch
Checkout branch: `task/task-auth` (will be created automatically)

## Deliverable
Implement JWT authentication middleware and login route.

## Files to Create/Modify
- src/auth/middleware.ts (create)
- src/auth/routes.ts (create)
- src/auth/types.ts (create)

## Acceptance Criteria
- [ ] POST /api/login accepts { email, password }, returns JWT token
- [ ] Middleware validates Authorization Bearer header
- [ ] Unit tests pass: pnpm test src/auth/
- [ ] Do NOT modify files outside src/auth/

## Constraints
- Use jsonwebtoken package (already installed)
- Token expires after 24h
- No session/cookie
""")

# 3. Run the task — reference the spec file
run_task(
    task_id="task-auth",
    cwd="/home/user/project",
    spec_file=".tasks/task-auth.md",
    isolation="branch",
)
```

`run_task` will:
1. Read the spec from `.tasks/task-auth.md` (appends "When Done" section if missing)
2. Create git branch `task/task-auth`
3. Spawn the opencode agent in a tmux window
4. Wait until the agent finishes
5. Read `.tasks/task-auth.result.md`
6. Update `.tasks/PROGRESS.md` with status
7. Return the result as JSON

### Fallback: Inline spec (small tasks only)

For trivial tasks (<10 lines of spec), you MAY pass `spec` inline:

```python
run_task(
    task_id="task-fix-typo",
    cwd="/home/user/project",
    spec="Fix typo in README.md: change 'recieve' to 'receive'.",
    isolation="none",
)
```

### Parallel Tasks (for independent features)

```python
# Write spec files first
Write(".tasks/task-auth.md", auth_spec)
Write(".tasks/task-payments.md", payments_spec)

# Start multiple tasks (returns immediately)
start_task("task-auth",     cwd="/project", spec_file=".tasks/task-auth.md",     isolation="worktree")
start_task("task-payments", cwd="/project", spec_file=".tasks/task-payments.md", isolation="worktree")

# Block until all finish
wait_tasks(["task-auth", "task-payments"], timeout=600)

# Check progress
get_progress("/project")

# Review results
auth_result = get_result("task-auth")
payments_result = get_result("task-payments")
```

**IMPORTANT:** Parallel tasks MUST use `isolation="worktree"` because two agents
cannot checkout different branches on the same working tree.

### Monitoring Progress

**Progress file** — auto-updated at `.tasks/PROGRESS.md`:
```python
get_progress("/project")
# → Returns markdown table with task status, duration, results preview
```

**Live terminal** — see agent working in real-time:
```bash
tmux attach -t oc-tasks    # Enter the team control room
# Ctrl-b w                 # List all task windows
# Ctrl-b n/p               # Switch between tasks
# Ctrl-b d                 # Detach back to terminal
```

**Peek** — check agent output from Claude Code:
```python
peek_task("task-auth")
# → Returns last 100 lines of tmux pane content
```

### Sending Feedback (code review)

If you're not happy with the result:

```python
send_feedback("task-auth", """
## Issues
1. Middleware doesn't handle expired tokens — should return 401 with clear message
2. Missing test cases for: expired token, malformed token

## Required Changes
- Add error handling for expired token in middleware.ts
- Add 2 test cases to auth.test.ts
- Update result file when done
""")
```

This writes `.tasks/task-auth.feedback.md` and notifies the agent if still running.

---

## Writing Good Specs

A weak spec produces weak output. A great spec produces production-ready code.
Always include these sections:

```markdown
## Project Context
<What is this project? Language/framework? Key file paths?>
<Paste relevant code snippets if the agent needs to understand existing patterns.>

## Working Branch
<Which branch to work on, or "current branch" if isolation=none>

## Deliverable
<Specific: what to implement, in which files, with what function/class names>

## Files to Create/Modify
<Explicit list with (create) or (modify) tags>

## Acceptance Criteria
- [ ] <Testable criterion>
- [ ] <Testable criterion>
- [ ] Tests pass: <specific test command>

## Constraints
- <Packages to use/avoid>
- <Files NOT to modify>
- <Style rules>
```

The **Project Context** section is the most common place specs fail. If you're not
sure what context to provide, first read the relevant files yourself, then include
the key parts.

---

## Isolation Strategy

| Situation | Isolation | Why |
|---|---|---|
| Single sequential task | `branch` (default) | Simple, teammate checks out branch |
| 2+ parallel tasks, different files | `worktree` | Each agent needs its own file system |
| 2+ parallel tasks, same file | ❌ Don't | Split differently, or run sequentially |
| Quick fix, trivial change | `none` | No branch overhead needed |
| Large project (node_modules/venv) | `branch` | Worktree would be slow to set up |

---

## Available MCP Tools (opencode-orchestrator v2)

| Tool | Blocking? | Purpose |
|------|-----------|---------|
| `run_task(task_id, cwd, spec_file, profile, ...)` | ✅ Yes | Run a task synchronously. **Use 90% of the time.** |
| `start_task(task_id, cwd, spec_file, profile, ...)` | ❌ Async | Start a task, return immediately. For parallel work. |
| `wait_tasks([ids], timeout?)` | ✅ Yes | Block until all tasks finish. |
| `get_result(task_id)` | ❌ | Read the .result.md file of a completed task. |
| `get_progress(cwd)` | ❌ | Read .tasks/PROGRESS.md — status table of all tasks. |
| `peek_task(task_id)` | ❌ | Capture tmux pane — see what agent is doing now. |
| `send_feedback(task_id, feedback)` | ❌ | Write feedback file + notify running agent. |
| `list_tasks(status?)` | ❌ | List all tasks, optionally filter by status. |
| `list_profiles(cwd?)` | ❌ | List all available specialty profiles. |

### Key parameters for run_task / start_task

| Parameter | Default | Description |
|-----------|---------|-------------|
| `task_id` | required | Unique ID, e.g. "task-auth" |
| `cwd` | required | Project root (absolute path) |
| `spec_file` | preferred | Path to pre-written spec .md file (absolute or relative to cwd) |
| `spec` | fallback | Inline spec string (only for trivial tasks) |
| `profile` | `None` | Specialty profile: `"be-dev"`, `"fe-dev"`, `"qa"`, `"ui-review"`, `"devops"`, `"security"` |
| `isolation` | `"branch"` | `"none"` / `"branch"` / `"worktree"` |
| `model` | `None` | Model override. If profile has default_model, uses that when not set. |
| `agent` | `"opencode"` | Agent plugin name |
| `timeout` | `600` | Max seconds (run_task only) |
| `visible` | `true` | Run in tmux (true) or headless (false) |

---

## File-based Communication

All communication happens via `.tasks/` directory in the project root:

```
project/
├── .tasks/
│   ├── PROGRESS.md                # Progress table (auto-updated)
│   ├── task-auth.md               # Spec (YOU write before calling run_task)
│   ├── task-auth.result.md        # Result (teammate writes)
│   ├── task-auth.log              # Terminal log (auto-captured)
│   └── task-auth.feedback.md      # Feedback (you write, optional)
```

**Workflow:**
1. YOU write `.tasks/task-auth.md` (the spec file)
2. Call `run_task(spec_file=".tasks/task-auth.md", ...)`
3. Server appends "When Done" instructions to spec if missing
4. Teammate reads spec → does work → writes `.tasks/task-auth.result.md`
5. Server reads result → updates `PROGRESS.md` → returns to you

---

## Example: Full Workflow

User: "Add user authentication to the Express API"

```
1. Read existing code: src/server.ts, src/routes/, package.json
2. Plan: auth needs middleware + routes + types + tests
3. Write the spec file:

   Write(".tasks/task-auth.md", """
   ## Project Context
   Express 4 + TypeScript. Routes in src/routes/, middleware in src/middleware/.
   Using jsonwebtoken (installed). Test with vitest.

   ## Deliverable
   JWT auth: login route + validation middleware.

   ## Files to Create
   - src/auth/middleware.ts
   - src/auth/routes.ts
   - src/auth/types.ts
   - src/auth/__tests__/auth.test.ts

   ## Acceptance Criteria
   - [ ] POST /api/login returns JWT
   - [ ] Middleware protects routes with Bearer token
   - [ ] pnpm test passes
   """)

4. Delegate:

   run_task(
     task_id="task-auth",
     cwd="/home/user/project",
     spec_file=".tasks/task-auth.md",
   )

5. Check progress: get_progress("/home/user/project")
6. Review result → check files, run tests yourself
7. If issues → send_feedback("task-auth", "...fix X...")
8. Merge branch: git merge task/task-auth
9. Report to human: "Auth implemented — login at POST /api/login, middleware protects routes"
```
