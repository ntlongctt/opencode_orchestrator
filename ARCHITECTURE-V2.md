# opencode-orchestrator v2 — Architecture Document

## Tổng quan

Redesign hoàn toàn MCP server để Claude Code (leader) điều phối OpenCode agents
(teammates) hiệu quả hơn. Lấy cảm hứng từ ComposioHQ/agent-orchestrator.

### Nguyên tắc thiết kế

1. **File-based communication** — spec file vào, result file ra. Không truyền prompt qua CLI arg, không capture PTY output.
2. **Tmux-based execution** — 1 session `oc-tasks`, mỗi agent là 1 window. Attach 1 lần, chuyển tab bằng phím tắt.
3. **Plugin architecture** — agent-agnostic, swap được OpenCode ↔ Aider ↔ bất kỳ tool nào.
4. **Worktree là optional** — mặc định dùng branch, chỉ dùng worktree khi parallel work thật sự cần isolation.
5. **Blocking-first API** — `run_task` blocking là default, async (`start_task` + `wait_tasks`) cho parallel work.

---

## Vấn đề của v1 và cách v2 giải quyết

| Vấn đề v1 | Nguyên nhân | Giải pháp v2 |
|---|---|---|
| Prompt quá dài → CLI arg limit | Nhét toàn bộ spec vào command line | Spec file `.tasks/<id>.md`, prompt chỉ là 1 dòng trỏ đến file |
| Output bị mất / ANSI lỗi | PTY capture TUI app (Ink.js) rồi strip ANSI | Teammate tự viết `.tasks/<id>.result.md`, đọc file trực tiếp |
| Không biết khi nào xong | Timeout + drain heuristic | Tmux session exit = done signal, kết hợp check result file |
| Headless, không monitor được | subprocess.Popen background | Tmux windows trong session `oc-tasks`, attach 1 lần xem tất cả |
| 3 tool calls cho 1 task | assign → wait → get_result | `run_task` = 1 blocking call, trả result luôn |
| Worker management phức tạp | spawn_worker / kill_worker riêng | Mỗi task tự quản lý process, không cần persistent worker |

---

## Cấu trúc project

```
opencode-orchestrator/
├── pyproject.toml
├── src/opencode_orchestrator/
│   ├── __init__.py
│   ├── server.py              # MCP tool definitions (7 tools)
│   ├── models.py              # Task, TaskConfig, IsolationMode
│   ├── task_manager.py        # Core: tạo spec, chạy tmux, đọc result
│   ├── tmux.py                # Tmux session management
│   └── agents/
│       ├── __init__.py
│       ├── base.py            # AgentPlugin ABC
│       └── opencode.py        # OpenCode implementation
├── templates/
│   ├── spec.md.j2             # Template cho spec file
│   └── result.md.j2           # Template hướng dẫn teammate viết result
└── ARCHITECTURE-V2.md         # (file này)
```

---

## Plugin System

### Interface (agents/base.py)

```python
from abc import ABC, abstractmethod

class AgentPlugin(ABC):
    """Interface cho mỗi loại coding agent."""

    name: str  # "opencode", "aider", "codex", ...

    @abstractmethod
    def build_command(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
    ) -> list[str]:
        """Trả về command list để subprocess chạy.

        prompt ở đây là prompt ngắn (1-2 câu), ví dụ:
        "Read .tasks/task-auth.md and complete the work. Write report to .tasks/task-auth.result.md"
        """
        ...

    @abstractmethod
    def build_env(self, model: str | None = None) -> dict:
        """Trả về environment variables cho subprocess."""
        ...

    @property
    @abstractmethod
    def supports_prompt_file(self) -> bool:
        """Agent có hỗ trợ --prompt-file hay --message-file không?
        Nếu có, task_manager sẽ dùng thay vì truyền prompt qua CLI arg."""
        ...
```

### OpenCode Plugin (agents/opencode.py)

```python
import os

class OpenCodeAgent(AgentPlugin):
    name = "opencode"

    def build_command(self, cwd, prompt, model=None):
        cmd = ["opencode", "run", "--dir", cwd]
        if model:
            cmd.extend(["-m", model])
        cmd.append(prompt)
        return cmd

    def build_env(self, model=None):
        env = os.environ.copy()
        if model:
            env["OPENCODE_MODEL"] = model
        return env

    @property
    def supports_prompt_file(self):
        return False  # opencode chưa hỗ trợ --prompt-file
```

### Thêm agent mới (ví dụ Aider)

```python
class AiderAgent(AgentPlugin):
    name = "aider"

    def build_command(self, cwd, prompt, model=None):
        cmd = ["aider", "--yes-always", "--no-git"]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["--message", prompt])
        return cmd

    @property
    def supports_prompt_file(self):
        return True  # aider hỗ trợ --message-file
```

---

## File-based Communication

### Thư mục .tasks/

Tất cả giao tiếp leader ↔ teammate đều qua `.tasks/` trong project root:

```
project/
├── .tasks/
│   ├── task-auth.md               # Spec: leader viết
│   ├── task-auth.result.md        # Result: teammate viết
│   ├── task-auth.log              # Log: tmux capture (auto)
│   ├── task-auth.feedback.md      # Feedback: leader viết (optional)
│   ├── task-payments.md
│   ├── task-payments.result.md
│   └── task-payments.log
├── src/
│   └── ...
```

**Quan trọng:** `.tasks/` nên thêm vào `.gitignore` — đây là communication channel,
không phải source code.

### Spec File Format (.tasks/<task_id>.md)

Leader viết hoặc MCP tự generate từ parameters:

```markdown
# Task: task-auth

## Project Context
Express 4 + TypeScript project. Cấu trúc: src/ chứa routes, middleware, models.
Package manager: pnpm. Test framework: vitest.

## Working Branch
Checkout branch mới: `task/task-auth` từ `main`

## Deliverable
Implement JWT authentication middleware và login route.

## Files to Create/Modify
- src/auth/middleware.ts (tạo mới)
- src/auth/routes.ts (tạo mới)
- src/auth/types.ts (tạo mới)

## Acceptance Criteria
- [ ] POST /api/login nhận { email, password }, trả về JWT token
- [ ] Middleware validateToken kiểm tra Authorization header
- [ ] Unit tests pass: pnpm test src/auth/
- [ ] Không sửa file ngoài phạm vi

## Constraints
- Dùng package jsonwebtoken (đã cài)
- Token expire sau 24h
- Không dùng session/cookie

## When Done
**BẮT BUỘC:** Viết report vào `.tasks/task-auth.result.md` với format:

- Status: DONE hoặc PARTIAL hoặc BLOCKED
- Summary: 2-3 câu mô tả việc đã làm
- Files Changed: danh sách file đã tạo/sửa
- Test Results: pass/fail + output ngắn
- Issues: vấn đề gặp phải (nếu có)
- Git: commit tất cả thay đổi trên branch
```

### Result File Format (.tasks/<task_id>.result.md)

Teammate viết sau khi hoàn thành:

```markdown
# Result: task-auth

## Status: DONE

## Summary
Đã implement JWT auth middleware và login route. Tạo 3 file mới trong src/auth/.
Middleware validateToken check Bearer token trong Authorization header.

## Files Changed
- src/auth/middleware.ts (tạo mới — 45 lines)
- src/auth/routes.ts (tạo mới — 62 lines)
- src/auth/types.ts (tạo mới — 12 lines)
- src/auth/__tests__/auth.test.ts (tạo mới — 38 lines)

## Test Results
✅ 8 tests passed, 0 failed
```
pnpm test src/auth/
 PASS  src/auth/__tests__/auth.test.ts
  ✓ POST /api/login returns JWT (12ms)
  ✓ validateToken accepts valid token (3ms)
  ...
```

## Issues
Không có vấn đề.

## Git
Committed on branch `task/task-auth`:
- "feat(auth): implement JWT middleware and login route"
```

### Feedback File (.tasks/<task_id>.feedback.md) — Optional

Leader gửi khi cần teammate sửa lại:

```markdown
# Feedback: task-auth

## Vấn đề
1. Middleware không handle trường hợp token expired — cần return 401 với message rõ ràng
2. Test thiếu case: invalid token format

## Yêu cầu
- Thêm error handling cho expired token
- Thêm 2 test cases: expired token, malformed token
- Cập nhật result file sau khi sửa
```

---

## Tmux Management (tmux.py)

### Kiến trúc: 1 Session, N Windows

Thay vì mỗi task = 1 tmux session riêng, tất cả tasks chạy trong **1 session
chung** tên `oc-tasks`. Mỗi task là 1 **window** (tab) trong session đó.

```
tmux server
└── Session "oc-tasks"                  ← 1 session duy nhất
    ├── Window 0: "task-auth"           ← agent đang code auth
    │   └── Pane 0: opencode running
    ├── Window 1: "task-payments"       ← agent đang code payments
    │   └── Pane 0: opencode running
    └── Window 2: "task-tests"          ← agent đang chạy tests
        └── Pane 0: opencode running
```

**User experience:**
```bash
# Vào "phòng điều khiển" của team
tmux attach -t oc-tasks

# Bên trong:
# Ctrl-b w       → list tất cả windows (tasks), chọn để jump
# Ctrl-b n / p   → next / previous task
# Ctrl-b 0-9     → jump to window by number
# Ctrl-b "       → split ngang, xem 2 tasks cùng lúc
# Ctrl-b %       → split dọc
# Ctrl-b d       → detach (quay lại terminal chính)
```

Lợi ích so với nhiều sessions:
- 1 lệnh attach duy nhất, không cần nhớ tên từng session
- Chuyển giữa tasks bằng phím tắt, giống tabs trong IDE
- Muốn so sánh 2 agents → user tự split pane
- Kill 1 window không ảnh hưởng window khác
- `tmux ls` gọn: chỉ 1 dòng thay vì N dòng

### Session name convention

```
SESSION_NAME = "oc-tasks"           # Session chung, cố định
WINDOW_TARGET = "oc-tasks:{task_id}"  # Trỏ đến window cụ thể
```

### Core functions

```python
import subprocess
import shutil
import logging

logger = logging.getLogger(__name__)

SESSION_NAME = "oc-tasks"


def tmux_available() -> bool:
    """Kiểm tra tmux có sẵn không."""
    return shutil.which("tmux") is not None


def _session_exists() -> bool:
    """Check session oc-tasks còn sống không."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", SESSION_NAME],
        capture_output=True,
    )
    return result.returncode == 0


def _wrap_command(command: str, log_file: str | None) -> str:
    """Wrap command để capture log nếu cần."""
    if log_file:
        # `script` ghi toàn bộ terminal output vào file
        # -q: quiet, -f: flush after each write
        return f"script -q -f {log_file} -c '{command}'"
    return command


def create_task_window(task_id: str, command: str, cwd: str,
                       log_file: str | None = None) -> None:
    """Tạo window mới cho task trong session oc-tasks.

    Nếu session chưa tồn tại → tạo session mới + window đầu tiên.
    Nếu session đã có → thêm window mới vào.
    """
    wrapped = _wrap_command(command, log_file)

    if not _session_exists():
        # Tạo session + window đầu tiên
        subprocess.run([
            "tmux", "new-session",
            "-d",                          # detached
            "-s", SESSION_NAME,            # session name
            "-n", task_id,                 # window name = task_id
            "-c", cwd,                     # working directory
            "bash", "-c", wrapped,
        ], check=True)
        logger.info("Created session '%s' with window '%s'",
                     SESSION_NAME, task_id)
    else:
        # Thêm window vào session có sẵn
        subprocess.run([
            "tmux", "new-window",
            "-t", SESSION_NAME,            # target session
            "-n", task_id,                 # window name
            "-c", cwd,                     # working directory
            "bash", "-c", wrapped,
        ], check=True)
        logger.info("Added window '%s' to session '%s'",
                     task_id, SESSION_NAME)


def window_exists(task_id: str) -> bool:
    """Check window cho task còn tồn tại không.

    Window tự biến mất khi command bên trong exit.
    Đây là cách ta biết agent đã hoàn thành.
    """
    target = f"{SESSION_NAME}:{task_id}"
    result = subprocess.run(
        ["tmux", "list-windows", "-t", SESSION_NAME, "-F", "#{window_name}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False  # Session không tồn tại
    windows = result.stdout.strip().split('\n')
    return task_id in windows


def wait_window_exit(task_id: str, timeout: int = 600,
                     poll_interval: float = 2.0) -> bool:
    """Block cho đến khi window (= task) kết thúc.

    Tmux tự xóa window khi command bên trong exit.
    Ta chỉ cần poll xem window còn không.

    Returns: True nếu window đã exit, False nếu timeout.
    """
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not window_exists(task_id):
            return True
        time.sleep(poll_interval)
    return False


def capture_pane(task_id: str, lines: int = 100) -> str:
    """Capture nội dung hiện tại của window.

    Dùng cho peek_task — xem agent đang làm gì.
    """
    target = f"{SESSION_NAME}:{task_id}"
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", target, "-p", "-S", f"-{lines}"],
        capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def send_keys(task_id: str, text: str) -> None:
    """Gửi text vào window của task.

    Dùng cho send_feedback — inject input vào agent đang chạy.
    Lưu ý: đây là gửi keystrokes, không phải stdin.
    """
    target = f"{SESSION_NAME}:{task_id}"
    subprocess.run(
        ["tmux", "send-keys", "-t", target, text, "Enter"],
        check=True,
    )


def kill_window(task_id: str) -> None:
    """Kill window của 1 task (không ảnh hưởng tasks khác)."""
    target = f"{SESSION_NAME}:{task_id}"
    subprocess.run(
        ["tmux", "kill-window", "-t", target],
        capture_output=True,
    )


def list_windows() -> list[str]:
    """List tất cả windows (= tasks đang chạy) trong session."""
    result = subprocess.run(
        ["tmux", "list-windows", "-t", SESSION_NAME, "-F", "#{window_name}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    return [w for w in result.stdout.strip().split('\n') if w]


def cleanup_session() -> None:
    """Xóa session khi không còn task nào.

    Gọi sau khi tất cả tasks hoàn thành.
    """
    if _session_exists() and not list_windows():
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME],
            capture_output=True,
        )
```

### Fallback khi không có tmux

Nếu tmux không available, fall back về subprocess trực tiếp. Vì communication
qua file (không qua PTY capture), **subprocess fallback vẫn hoạt động tốt** —
chỉ mất khả năng monitoring:

```python
def _run_subprocess_fallback(cmd, cwd, log_file, timeout):
    """Chạy agent bằng subprocess thường.

    Không có monitoring nhưng vẫn hoạt động
    vì communication qua file, không phụ thuộc PTY.
    """
    with open(log_file, "w") as log:
        proc = subprocess.Popen(
            cmd, cwd=cwd,
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return False
    return True
```

**Điểm quan trọng:** Đây là improvement lớn nhất so với v1. Vì communication qua
file, ta không phụ thuộc PTY capture nữa. Opencode vẫn chạy, vẫn đọc spec file,
viết code, viết result file — dù có tmux hay không.

---

## Isolation Strategy

### 3 chế độ

```python
class IsolationMode(str, Enum):
    NONE = "none"          # Làm trên branch hiện tại, không tạo gì
    BRANCH = "branch"      # Checkout branch mới (mặc định)
    WORKTREE = "worktree"  # Tạo git worktree riêng
```

### Khi nào dùng gì

| Tình huống | Isolation | Lý do |
|---|---|---|
| Task đơn, tuần tự | `branch` | Đơn giản, teammate checkout branch, code, commit |
| 2+ tasks song song, sửa file khác nhau | `worktree` | Cần file system riêng, tránh conflict |
| 2+ tasks song song, sửa cùng file | ❌ Không nên | Split task khác đi, hoặc chạy tuần tự |
| Task nhỏ, quick fix | `none` | Sửa trực tiếp, không cần branch |
| Project có node_modules/venv lớn | `branch` | Worktree phải copy/symlink deps, quá chậm |

### Implementation

```python
def _setup_isolation(self, task_id: str, project_cwd: str,
                     mode: IsolationMode) -> str:
    """Setup isolation và trả về cwd thực tế cho agent.

    Returns: cwd path mà agent sẽ chạy trong đó
    """
    branch_name = f"task/{task_id}"

    if mode == IsolationMode.NONE:
        return project_cwd

    elif mode == IsolationMode.BRANCH:
        # Tạo branch mới nhưng vẫn dùng cùng cwd
        # Agent sẽ tự checkout branch trong spec
        subprocess.run(
            ["git", "branch", branch_name],
            cwd=project_cwd, capture_output=True,
        )
        return project_cwd

    elif mode == IsolationMode.WORKTREE:
        worktree_path = f"/tmp/oc-worktree-{task_id}"
        subprocess.run(
            ["git", "worktree", "add", worktree_path, "-b", branch_name],
            cwd=project_cwd, check=True,
        )
        return worktree_path
```

### Cleanup

```python
def _cleanup_isolation(self, task_id: str, project_cwd: str,
                       mode: IsolationMode) -> None:
    """Cleanup sau khi task hoàn thành."""
    if mode == IsolationMode.WORKTREE:
        worktree_path = f"/tmp/oc-worktree-{task_id}"
        subprocess.run(
            ["git", "worktree", "remove", worktree_path, "--force"],
            cwd=project_cwd, capture_output=True,
        )
        # Không xóa branch — leader sẽ merge hoặc xóa
```

---

## MCP Tools (server.py)

### 7 Tools

```python
@mcp.tool()
def run_task(
    task_id: str,
    spec: str,
    cwd: str,
    isolation: str = "branch",
    model: str | None = None,
    agent: str = "opencode",
    timeout: int = 600,
    visible: bool = True,
) -> str:
    """Run a coding task synchronously. Blocks until the agent completes.

    This is the primary tool — use for most tasks.

    Args:
        task_id:   Unique ID (e.g. "task-auth"). Used for file names and tmux session.
        spec:      Task specification in markdown. Will be written to .tasks/<task_id>.md
        cwd:       Project root directory (absolute path).
        isolation: "none" | "branch" (default) | "worktree"
        model:     Optional model override (e.g. "kimi/moonshot-v1-8k")
        agent:     Agent plugin to use (default: "opencode")
        timeout:   Max seconds to wait (default: 600)
        visible:   If true, run in tmux (attachable). If false, run headless with log file.

    Returns:
        JSON with: status, result (content of .result.md), duration, files_changed
    """
    ...


@mcp.tool()
def start_task(
    task_id: str,
    spec: str,
    cwd: str,
    isolation: str = "branch",
    model: str | None = None,
    agent: str = "opencode",
    visible: bool = True,
) -> str:
    """Start a coding task asynchronously. Returns immediately.

    Use for parallel work: start multiple tasks, then wait_tasks().

    Same args as run_task minus timeout.
    """
    ...


@mcp.tool()
def wait_tasks(task_ids: list[str], timeout: int = 600) -> str:
    """Block until all specified tasks complete.

    Args:
        task_ids: List of task IDs to wait for
        timeout:  Max seconds to wait (default: 600)

    Returns:
        JSON with status of each task and overall summary.
    """
    ...


@mcp.tool()
def get_result(task_id: str) -> str:
    """Read the result file for a completed task.

    Returns the full content of .tasks/<task_id>.result.md
    If no result file exists, returns the task log instead.
    """
    ...


@mcp.tool()
def peek_task(task_id: str) -> str:
    """See what an agent is currently doing.

    Captures the last 100 lines of the tmux pane.
    Use to check progress on long-running tasks.
    """
    ...


@mcp.tool()
def send_feedback(task_id: str, feedback: str) -> str:
    """Send feedback to a running agent.

    Writes feedback to .tasks/<task_id>.feedback.md.
    If the agent is still running in tmux, also sends a notification
    to the tmux session.

    Args:
        task_id:  The task to send feedback to
        feedback: Markdown content describing what needs to change
    """
    ...


@mcp.tool()
def list_tasks(status: str | None = None) -> str:
    """List all tasks with their current status.

    Args:
        status: Optional filter — "running", "done", "failed"
    """
    ...
```

---

## Core Flow: run_task (chi tiết)

```
Leader gọi: run_task("task-auth", spec="## Deliverable\nImplement JWT...",
                     cwd="/home/user/project", isolation="branch")

╔══════════════════════════════════════════════════════════════╗
║ 1. WRITE SPEC FILE                                          ║
║                                                              ║
║   Tạo .tasks/ directory nếu chưa có                         ║
║   Viết spec → .tasks/task-auth.md                           ║
║   Thêm instruction "When Done" section tự động              ║
╚══════════════════════════════════════════════════════════════╝
                          │
╔══════════════════════════════════════════════════════════════╗
║ 2. SETUP ISOLATION                                           ║
║                                                              ║
║   isolation="branch" →                                       ║
║     git branch task/task-auth (tạo branch mới)              ║
║     Thêm vào spec: "git checkout task/task-auth trước khi   ║
║     bắt đầu"                                                ║
║                                                              ║
║   isolation="worktree" →                                     ║
║     git worktree add /tmp/oc-worktree-task-auth              ║
║     cwd thay đổi thành worktree path                        ║
╚══════════════════════════════════════════════════════════════╝
                          │
╔══════════════════════════════════════════════════════════════╗
║ 3. BUILD COMMAND                                             ║
║                                                              ║
║   agent_plugin = get_plugin("opencode")                      ║
║   prompt = "Read .tasks/task-auth.md and complete all work.  ║
║            Write report to .tasks/task-auth.result.md"       ║
║   cmd = agent_plugin.build_command(cwd, prompt, model)       ║
║   → ["opencode", "run", "--dir", "/home/user/project",      ║
║      "Read .tasks/task-auth.md and ..."]                     ║
╚══════════════════════════════════════════════════════════════╝
                          │
╔══════════════════════════════════════════════════════════════╗
║ 4. EXECUTE IN TMUX (1 session, N windows)                    ║
║                                                              ║
║   tmux.create_task_window(                                   ║
║     task_id="task-auth",                                     ║
║     command=cmd_string,                                      ║
║     cwd=effective_cwd,                                       ║
║     log_file=".tasks/task-auth.log"                         ║
║   )                                                          ║
║                                                              ║
║   → Nếu session "oc-tasks" chưa có → tạo mới               ║
║   → Nếu đã có → thêm window "task-auth" vào                ║
║                                                              ║
║   User: tmux attach -t oc-tasks                              ║
║   → Ctrl-b w để list windows, Ctrl-b n/p chuyển tab        ║
╚══════════════════════════════════════════════════════════════╝
                          │
╔══════════════════════════════════════════════════════════════╗
║ 5. WAIT FOR COMPLETION                                       ║
║                                                              ║
║   tmux.wait_window_exit("task-auth", timeout=600)           ║
║   │                                                          ║
║   ├── Window tự biến mất khi command exit → tiếp bước 6    ║
║   └── Timeout → kill window, check result file anyway       ║
╚══════════════════════════════════════════════════════════════╝
                          │
╔══════════════════════════════════════════════════════════════╗
║ 6. COLLECT RESULT                                            ║
║                                                              ║
║   if .tasks/task-auth.result.md exists:                      ║
║     result = read file content                               ║
║     status = parse "## Status:" line                        ║
║   else:                                                      ║
║     result = read .tasks/task-auth.log (fallback)           ║
║     status = "unknown"                                       ║
║                                                              ║
║   Detect files changed: git diff --name-only task/task-auth ║
╚══════════════════════════════════════════════════════════════╝
                          │
╔══════════════════════════════════════════════════════════════╗
║ 7. CLEANUP + RETURN                                          ║
║                                                              ║
║   if isolation=="worktree": remove worktree                  ║
║   Return JSON:                                               ║
║     {                                                        ║
║       "ok": true,                                            ║
║       "task_id": "task-auth",                                ║
║       "status": "done",                                      ║
║       "result": "<content of result.md>",                    ║
║       "duration_s": 127.3,                                   ║
║       "files_changed": ["src/auth/middleware.ts", ...],      ║
║       "branch": "task/task-auth"                             ║
║     }                                                        ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Parallel Workflow

```python
# Leader giao 2 tasks song song (sửa file khác nhau)
start_task("task-auth", spec=auth_spec, cwd="/project", isolation="worktree")
start_task("task-payments", spec=pay_spec, cwd="/project", isolation="worktree")

# Đợi cả 2 xong
wait_tasks(["task-auth", "task-payments"], timeout=600)

# Lấy kết quả
auth_result = get_result("task-auth")
pay_result = get_result("task-payments")

# Leader review, merge branches
# git merge task/task-auth
# git merge task/task-payments
```

**Lưu ý:** Parallel tasks PHẢI dùng `isolation="worktree"` vì 2 agent không thể
checkout 2 branch khác nhau trên cùng worktree.

---

## CI Feedback Loop

### Lớp 1: Teammate tự test (trong spec)

Spec file luôn có section yêu cầu teammate chạy test:

```markdown
## Acceptance Criteria
- [ ] Unit tests pass: pnpm test src/auth/
```

Teammate có đầy đủ quyền chạy shell commands (OpenCode có terminal access),
nên sẽ tự chạy test và ghi kết quả vào result file.

### Lớp 2: Leader review + send_feedback

Khi leader không hài lòng với kết quả:

```python
# Đọc result, thấy thiếu test case
send_feedback("task-auth", """
## Vấn đề
1. Thiếu test case cho expired token
2. Middleware không handle malformed Authorization header

## Yêu cầu
- Thêm 2 test cases
- Fix error handling
- Cập nhật result file
""")
```

`send_feedback` sẽ:
1. Viết feedback vào `.tasks/task-auth.feedback.md`
2. Nếu tmux session vẫn chạy → `tmux send-keys` để notify agent
3. Nếu session đã exit → leader có thể `run_task` lại với spec mới (re-work)

### Tương lai (Lớp 3): Auto CI webhook

Sau này có thể thêm webhook listener:
- CI pipeline fail → tự động tạo feedback file
- Leader nhận notification và quyết định có re-assign không

Nhưng giai đoạn đầu, lớp 1 + 2 là đủ.

---

## Monitoring

### Chế độ Visible (mặc định, visible=True)

```bash
# Vào "phòng điều khiển" — 1 lệnh duy nhất
tmux attach -t oc-tasks

# Bên trong session, mỗi task là 1 tab (window):
# ┌─────────────┬──────────────────┬────────────────┐
# │ task-auth * │ task-payments    │ task-tests     │
# └─────────────┴──────────────────┴────────────────┘
#
# Ctrl-b w       → list windows, chọn bằng arrow keys
# Ctrl-b n / p   → next / previous window
# Ctrl-b 0-9     → jump to window by number
#
# Muốn xem 2 agents cùng lúc:
# Ctrl-b "       → split ngang (2 panes trên/dưới)
# Ctrl-b %       → split dọc (2 panes trái/phải)
#
# Ctrl-b d       → detach, quay lại terminal chính
```

### Chế độ Headless (visible=False)

Agent chạy bằng subprocess, output ghi vào log file:

```bash
# Xem log real-time
tail -f .tasks/task-auth.log
```

### Peek từ Leader (MCP tool)

```python
# Leader dùng peek_task để xem mà không cần attach
peek_task("task-auth")
# → Capture 100 dòng cuối của window "task-auth" trong session oc-tasks
```

---

## Error Handling

### Teammate không viết result file

Có thể xảy ra nếu model yếu hoặc agent crash giữa chừng.

```python
def _collect_result(self, task_id, cwd):
    result_path = Path(cwd) / ".tasks" / f"{task_id}.result.md"
    log_path = Path(cwd) / ".tasks" / f"{task_id}.log"

    if result_path.exists():
        return {"status": "done", "result": result_path.read_text()}

    elif log_path.exists():
        log_content = log_path.read_text()
        return {
            "status": "unknown",
            "result": None,
            "log_tail": log_content[-2000:],  # Last 2000 chars
            "warning": "No result file written. Check log and working directory."
        }

    else:
        return {
            "status": "failed",
            "result": None,
            "error": "No result file and no log found."
        }
```

### Tmux không available

Fall back về subprocess. Vì communication qua file, vẫn hoạt động — chỉ mất
khả năng monitoring.

### Task timeout

```python
if not tmux.wait_session_exit(session, timeout):
    tmux.kill_session(session)
    result = self._collect_result(task_id, cwd)
    result["warning"] = f"Task timed out after {timeout}s"
    return result
```

---

## Migration từ v1 → v2

### Breaking changes

1. **Bỏ `spawn_worker` / `kill_worker` / `list_workers`** — không cần persistent worker nữa
2. **`assign_task` → `start_task`** — đổi tên, đổi interface (spec thay vì prompt)
3. **Thêm `run_task`** — blocking, tool chính
4. **Thêm `peek_task` / `send_feedback`** — monitoring + feedback
5. **Bỏ PTY capture** — không cần nữa

### CLAUDE.md cần update

Skill file `opencode-team` cần rewrite để dùng tools mới.
Thay vì spawn_worker → assign_task → wait → get_result,
chỉ cần: run_task (blocking) hoặc start_task + wait_tasks (parallel).

### Dependencies mới

- `tmux` cần cài trên máy (hầu hết macOS/Linux đã có)
- Không thêm Python dependency mới — subprocess gọi tmux CLI

---

## Tóm tắt so sánh v1 vs v2

| Aspect | v1 | v2 |
|---|---|---|
| Communication | CLI arg + PTY capture | File-based (.md) |
| Execution | subprocess.Popen + PTY | 1 tmux session, N windows (tabs) |
| Monitoring | Không có | `tmux attach -t oc-tasks` → tab giữa tasks |
| Feedback | Không có | send_feedback + feedback file |
| Agent type | OpenCode only | Plugin architecture |
| Tool calls cho 1 task | 3 (assign + wait + get) | 1 (run_task) |
| Worktree | Luôn đề xuất dùng | Optional, mặc định branch |
| PTY dependency | Bắt buộc | Không cần |
| tmux dependency | Không | Có (fallback subprocess nếu không có) |
| Persistent worker | Có (spawn_worker) | Không cần |
| Nhiều tasks cùng lúc | N sessions rời rạc | N windows trong 1 session |
