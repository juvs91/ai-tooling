# ADR-0003: Proxy Code Organization Refactoring

- **Date**: 2026-04-28
- **Status**: Accepted
- **Deciders**: Jorge Guzman

---

## Context

The proxy codebase had three minor code organization issues:

1. **`litellm` import inside function** — In `compressor.py`, `import litellm` was inside `_llm_compress_single()` function instead of at module level. This violates Python best practice: imports should typically be at the top of modules for clarity and to avoid repeated import overhead.

2. **Duplicate `TYPE_CHECKING` import** — In `quality_refinement.py`, `from typing import TYPE_CHECKING` was imported twice (lines 23 and 25). The duplicate was redundant.

3. **Logging setup before imports** — In `server.py`, `logging.basicConfig()` and `warnings.filterwarnings()` were called at the top before other module imports. While logging configuration should happen early, it's cleaner to group all imports first, then configuration.

---

## Decision

### 1. Move litellm import to module level (compressor.py)

Moved `import litellm` from inside `_llm_compress_single()` to the top of the module with other imports.

**Rationale**: Python PEP 8 recommends that "Imports are always put at the top of the file". This improves code readability and ensures the import happens once at module load time, not on every function call.

### 2. Remove duplicate TYPE_CHECKING import (quality_refinement.py)

Removed the duplicate `from typing import TYPE_CHECKING` on line 25.

**Rationale**: The first import (line 23) is sufficient. Duplicate imports add noise without benefit.

### 3. Reorder logging/warnings setup after imports (server.py)

Moved `logging.basicConfig()` and `warnings.filterwarnings()` calls after all other imports, before `load_dotenv()`.

**Rationale**: Grouping all imports together makes the module's dependencies clearer at a glance. Logging configuration still happens before application logic, just after imports.

---

## Consequences

**Positive:**
- Code follows PEP 8 import conventions more closely
- Improved readability — all imports grouped at the top of each module
- Eliminates redundant imports
- Minor performance improvement (litellm imported once at module load vs. per function call)

**Negative / Trade-offs:**
- None — these are pure code organization changes with no functional impact

---

## Files Modified

| File | Change |
|------|--------|
| `llm/compressor.py` | Move `import litellm` to module level (line 26) |
| `llm/transformers/quality_refinement.py` | Remove duplicate TYPE_CHECKING import (line 25) |
| `server.py` | Move logging/warnings config after imports (lines 49-57) |