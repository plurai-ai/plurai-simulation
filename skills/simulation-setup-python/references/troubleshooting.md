# Troubleshooting

Read this from the head of Step 10 when the smoke run fails with an import,
interception, or checkpointer symptom that isn't covered by Step 10's own
error-and-fix table.

## Import fails despite stubs
Read the traceback. Find which import failed. Add another `sys.modules` line. The most common miss: a dependency that's imported indirectly through another module.

## Agent crashes with "NoneType has no attribute..."
A context-loading node wasn't replaced. It reads from MagicMock, which returns MagicMock objects — not the strings/dicts the agent expects. Add a replacement node in Phase 3.

## Tool calls not intercepted
Wrong node name. Check `{graph}.nodes.keys()` to find the actual tools node name. It might be `"tools"`, `"tool_executor"`, `"action"`, or custom.

## Module-level code uses a DB result
```python
CONFIG = db.query(SystemConfig).first()
CURRENCY = CONFIG.currency  # MagicMock, not "USD"
```
Fix: after import, patch the value:
```python
import {module}
{module}.CURRENCY = "USD"
```

## Tool in "real" mode returns MagicMock
Its dependency is stubbed in Phase 1. Don't stub modules that real-mode tools depend on.

## Tools not intercepted despite apply_mocks
The agent imported the tool function BEFORE apply_mocks ran. Fix: move apply_mocks earlier in simulate.py — it must run before the agent module import. Remember: `from tools import func` copies the function reference at import time. If apply_mocks patches `tools.func` after the agent already grabbed the old reference, the agent still has the unpatched copy.

## Agent compiled with production checkpointer and crashes
The checkpointer's dependencies (psycopg2, redis, etc.) should be stubbed in Phase 1. If the stub causes the checkpointer to fail silently, that's fine — the agent still runs, just without persistence. If it crashes hard, stub the checkpointer class itself: `sys.modules["langgraph.checkpoint.postgres"] = MagicMock()`.
