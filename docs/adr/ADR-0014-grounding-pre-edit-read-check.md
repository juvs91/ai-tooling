# ADR-0014: Grounding Pre-Edit Read Check

**Status:** Accepted  
**Date:** 2026-07-01  
**Author:** juvs

## Context

`grounding_validator.py` validates citations in response text against files that were
actually read. However, it does not verify that a file being **edited** (Edit/Write/MultiEdit
tool call) was read prior to the edit. A model can call `Edit(file_path="x.tsx", new_string="...")`
based on hallucinated assumptions about the file's content, silently replacing real code.

The evidence map (`evidence_map`) is already built by the transformer and includes both
current-session reads and historical reads restored from session cache after compression
boundaries (`get_session_read_files()`). The data exists — it just wasn't used for this check.

## Decision

Add a module-level pure function `_extract_edit_paths()` and integrate a "Step 2.5"
into `GroundingValidatorTransformer.transform_response()` that:

1. Collects paths of existing files targeted by Edit/MultiEdit/Write tool calls
2. Computes the set difference against `evidence_map` (already complete at insertion point)
3. If the difference is non-empty, injects a system note warning via `ensure_system_note()`

Implementation uses set difference (not if/else chains) to stay consistent with
the project's preference for declarative filtering over nested branching.

**Soft warning (not hard block):** The check injects a system note but does not replace
the tool_use block. Rationale:
- Hard blocking would break legitimate edits of well-known config files or templates
- `evidence_map` already handles pre-compression reads (historical_files) so false positives
  are rare in practice
- Escalation path: if warnings are too noisy, promote to block (plan_mode_guard pattern)

## Consequences

- Models that edit files without reading them first receive a warning in the next request
- Zero new if/else branches in `transform_response()` body
- `ctx.unread_edit_files` is populated for observability (future telemetry use)
- `os.path.exists()` call per Edit block — negligible overhead (local filesystem)
