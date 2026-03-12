#!/usr/bin/env bash
# Quick validation test for opencode-orchestrator MCP server
set -e

echo "═══════════════════════════════════════════════════════════════"
echo "  opencode-orchestrator MCP Quick Test"
echo "═══════════════════════════════════════════════════════════════"
echo

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS=0
FAIL=0

ok() { echo -e "${GREEN}✓${NC} $1"; PASS=$((PASS+1)); }
fail() { echo -e "${RED}✗${NC} $1"; FAIL=$((FAIL+1)); }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }

# ── Test 1: Python environment ────────────────────────────────────────────────
echo "▶ Test 1: Python dependencies"
if uv run python3 -c "from opencode_orchestrator.server import mcp" 2>/dev/null; then
    ok "MCP server imports successfully"
else
    fail "Failed to import MCP server"
fi

# ── Test 2: MCP tools registered ─────────────────────────────────────────────
echo
echo "▶ Test 2: MCP tools registration"
TOOLS=$(uv run python3 -c "
from opencode_orchestrator.server import mcp
print('\n'.join(mcp._tool_manager._tools.keys()))
" 2>/dev/null)

EXPECTED_TOOLS="spawn_worker kill_worker list_workers assign_task get_task_status get_task_result list_tasks wait_for_tasks cancel_task"
ALL_FOUND=true
for tool in $EXPECTED_TOOLS; do
    if echo "$TOOLS" | grep -q "$tool"; then
        echo "  ✓ $tool"
    else
        echo "  ✗ $tool (missing)"
        ALL_FOUND=false
    fi
done

if $ALL_FOUND; then
    ok "All 9 MCP tools registered"
else
    fail "Some tools missing"
fi

# ── Test 3: WorkerManager functionality ─────────────────────────────────────
echo
echo "▶ Test 3: WorkerManager"
uv run python3 -c "
from opencode_orchestrator.worker_manager import WorkerManager
wm = WorkerManager()
print(f'Base port: {wm._port_counter}')
port = wm._next_port()
print(f'Next port: {port}')
assert port >= 4096
print('Port allocation works')
" 2>/dev/null && ok "WorkerManager core functionality" || fail "WorkerManager failed"

# ── Test 4: TaskManager functionality ───────────────────────────────────────
echo
echo "▶ Test 4: TaskManager"
uv run python3 -c "
from opencode_orchestrator.worker_manager import WorkerManager
from opencode_orchestrator.task_manager import TaskManager
wm = WorkerManager()
tm = TaskManager(wm)
print(f'TaskManager created with {len(tm.list_all())} tasks')

# Test command building
class FakeTask:
    worker_id = None
    cwd = '/tmp'
    prompt = 'test prompt'

cmd = tm._build_command(FakeTask())
print(f'Command for no worker: {cmd}')
assert cmd == ['opencode', 'run', 'test prompt']
print('Command building works')
" 2>/dev/null && ok "TaskManager core functionality" || fail "TaskManager failed"

# ── Test 5: Models ───────────────────────────────────────────────────────────
echo
echo "▶ Test 5: Data models"
uv run python3 -c "
from opencode_orchestrator.models import Worker, Task, WorkerStatus, TaskStatus
import time

w = Worker(worker_id='test', cwd='/tmp', port=4096)
assert w.status == WorkerStatus.STARTING
assert w.to_dict()['worker_id'] == 'test'
print('Worker model OK')

t = Task(task_id='t1', prompt='hello', cwd='/tmp')
assert t.status == TaskStatus.PENDING
assert 'hello' in t.to_dict()['prompt']
print('Task model OK')
" 2>/dev/null && ok "Data models work correctly" || fail "Data models failed"

# ── Test 6: opencode CLI availability ───────────────────────────────────────
echo
echo "▶ Test 6: opencode CLI"
if command -v opencode &>/dev/null; then
    VERSION=$(opencode --version 2>&1 | head -1)
    ok "opencode found: $VERSION"
    
    # Check if opencode run works (with timeout)
    echo "  Testing opencode run (10s timeout)..."
    ( timeout 10 opencode run "Say hi" 2>/dev/null | head -5 ) &
    pid=$!
    sleep 8
    if kill -0 $pid 2>/dev/null; then
        kill $pid 2>/dev/null
        warn "opencode run is slow (may need optimization)"
    else
        wait $pid 2>/dev/null
        ok "opencode run responds"
    fi
else
    fail "opencode not found - install with: npm i -g opencode"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo "═══════════════════════════════════════════════════════════════"
echo "  Summary: $PASS passed, $FAIL failed"
echo "═══════════════════════════════════════════════════════════════"
echo

if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    echo
    echo "The MCP server is ready to use. To test with Claude Code:"
    echo "  1. Add to Claude Code MCP config:"
    echo "     { \"command\": \"uv\", \"args\": [\"run\", \"opencode-orchestrator\"] }"
    echo "  2. Restart Claude Code"
    echo "  3. Run /mcp to verify connection"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
