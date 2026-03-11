# Comprehensive Catalog: tool_prompting.py Functions

**Generated:** 2026-03-10
**Purpose:** Migration planning for tool_prompting.py deprecation
**Analysis:** Complete function catalog with usage tracking and universal_tool_extraction comparison

---

## Executive Summary

**Total Functions/Classes in tool_prompting.py:** 28
- **Extraction functions (needed for response processing):** 12
- **Request preprocessing (tool_prompting only):** 6
- **Utility/helper functions (needed):** 10
- **Classes/structures (needed):** 1

**Functions imported by universal_tool_extraction.py:** 8
**Functions used by other modules:** 15

---

## Section 1: Extraction Functions (Response Processing)

These functions are essential for processing model responses and extracting tool calls.

### Function: extract_tool_calls_from_text
**Line:** 695
**Purpose:** Extract XML tool calls from text response with multiple fallback strategies
**Signature:** `def extract_tool_calls_from_text(text: str, valid_tool_names: set[str] | None = None, tools: list | None = None) -> tuple[list[dict], str]`
**Status:** ESSENTIAL - Core extraction function
**Current Usage:**
- `universal_tool_extraction.py` (imported and used)
- `converters.py` (imported and used)
- `streaming.py` (imported and used)
- `reasoning_handling.py` (imported and used)
**In universal_tool_extraction:** YES (imported from tool_prompting)

### Function: _safe_parse_tool_input
**Line:** 639
**Purpose:** Parse and validate tool input JSON with multiple fallback strategies (JSON repair, XML-as-tags, schema cleanup)
**Signature:** `def _safe_parse_tool_input(raw_input: str, tool_name: str, tools: list | None = None) -> dict`
**Status:** ESSENTIAL - Core input parsing for extract_tool_calls_from_text
**Current Usage:** Internal helper for extract_tool_calls_from_text
**In universal_tool_extraction:** NO (needed by extract_tool_calls_from_text which is imported)

### Function: _strip_inner_xml_tags
**Line:** 473
**Purpose:** Strip inner XML wrappers (input, textarea, arguments, params) from tool input
**Signature:** `def _strip_inner_xml_tags(raw: str) -> str`
**Status:** ESSENTIAL - Used by _safe_parse_tool_input
**Current Usage:** Internal helper for _safe_parse_tool_input
**In universal_tool_extraction:** NO (used internally by imported functions)

### Function: _parse_xml_as_tags
**Line:** 501
**Purpose:** Parse XML-as-tags format (e.g., <file_path>...</file_path><content>...</content>)
**Signature:** `def _parse_xml_as_tags(raw: str, tool_name: str, tools: list | None = None) -> dict | None`
**Status:** ESSENTIAL - Fallback parsing strategy
**Current Usage:**
- Internal helper for _safe_parse_tool_input
- `universal_tool_extraction.py` (imported directly)
- `reasoning_handling.py` (imported directly)
**In universal_tool_extraction:** YES (imported from tool_prompting)

### Function: _greedy_extract_json_fields
**Line:** 552
**Purpose:** Greedy JSON field extraction with regex fallback for malformed JSON
**Signature:** `def _greedy_extract_json_fields(raw: str, tool_name: str, tools: list | None) -> dict | None`
**Status:** ESSENTIAL - Fallback extraction strategy
**Current Usage:** Internal helper for _safe_parse_tool_input
**In universal_tool_extraction:** NO (used internally by imported functions)

### Function: _schema_aware_cleanup
**Line:** 611
**Purpose:** Clean and validate parsed input against tool schema, convert types, add missing fields
**Signature:** `def _schema_aware_cleanup(parsed: dict, tool_name: str, tools: list | None) -> dict`
**Status:** ESSENTIAL - Schema validation and type conversion
**Current Usage:** Internal helper for _safe_parse_tool_input
**In universal_tool_extraction:** NO (used internally by imported functions)

### Function: _parse_argkv_tool
**Line:** 448
**Purpose:** Parse GLM arg_key/arg_value format
**Signature:** `def _parse_argkv_tool(match) -> dict`
**Status:** ESSENTIAL - GLM format parsing
**Current Usage:**
- Internal helper for extract_tool_calls_from_text
- `universal_tool_extraction.py` (imported directly)
**In universal_tool_extraction:** YES (imported from tool_prompting)

### Function: strip_tool_call_xml
**Line:** 1180
**Purpose:** Strip all tool_call XML variants from text (cleanup function)
**Signature:** `def strip_tool_call_xml(text: str) -> str`
**Status:** ESSENTIAL - Text cleanup after extraction
**Current Usage:**
- `converters.py` (imported and used)
- `streaming.py` (imported and used)
- `universal_tool_extraction.py` (imported)
- `reasoning_handling.py` (imported and used)
**In universal_tool_extraction:** YES (imported from tool_prompting)

### Function: _normalize_escaped_xml
**Line:** 1220
**Purpose:** Unescape JSON-encoded XML that leaked from content strings
**Signature:** `def _normalize_escaped_xml(xml: str) -> str | None`
**Status:** ESSENTIAL - Handle escaped XML in model outputs
**Current Usage:**
- Internal helper for extract_tool_calls_from_text
- `universal_tool_extraction.py` (imported directly)
**In universal_tool_extraction:** YES (imported from tool_prompting)

### Function: recover_truncated_deterministic
**Line:** 956
**Purpose:** Recover truncated tool_call XML deterministically using json_repair
**Signature:** `def recover_truncated_deterministic(partial_xml: str, tools: list | None = None) -> list[dict] | None`
**Status:** ESSENTIAL - Recovery for incomplete tool calls
**Current Usage:**
- `streaming.py` (imported via recover_incomplete_tool_call)
- `converters.py` (imported and used)
- Internal helper for recover_incomplete_tool_call
**In universal_tool_extraction:** NO (but needed by streaming and converters)

### Function: recover_incomplete_tool_call
**Line:** 1084
**Purpose:** Async recovery of incomplete tool calls (deterministic + LLM fallback)
**Signature:** `async def recover_incomplete_tool_call(partial_xml: str, tools: list | None, model: str, api_key: str, api_base: str | None = None, timeout_s: float = 3.0) -> list[dict] | None`
**Status:** ESSENTIAL - Streaming recovery function
**Current Usage:**
- `streaming.py` (imported and used)
**In universal_tool_extraction:** NO (streaming-specific)

### Function: _repair_tool_input
**Line:** 845
**Purpose:** Repair tool input using schema and type compatibility checks
**Signature:** `def _repair_tool_input(name: str, input_dict: dict, tools: list | None) -> dict`
**Status:** ESSENTIAL - Input repair/validation
**Current Usage:** Internal helper for _safe_parse_tool_input
**In universal_tool_extraction:** NO (used internally by imported functions)

---

## Section 2: Request Preprocessing Functions (tool_prompting Only)

These functions are ONLY used for preprocessing requests BEFORE sending to models. They can be marked for deletion.

### Function: build_tool_prompt
**Line:** 249
**Purpose:** Build tool prompt for no-tools models (system instruction)
**Signature:** `def build_tool_prompt(tools: list[dict]) -> str`
**Status:** REQUEST_PREPROCESSING - Can be deleted
**Current Usage:**
- `converters.py` (imported and used)
**In universal_tool_extraction:** NO (request preprocessing only)

### Function: _format_schema_properties
**Line:** 101
**Purpose:** Format JSON Schema properties into readable parameter list with recursion
**Signature:** `def _format_schema_properties(input_schema: dict, depth: int = 0, max_depth: int = 2) -> str`
**Status:** REQUEST_PREPROCESSING - Helper for build_tool_prompt
**Current Usage:** Internal helper for build_tool_prompt
**In universal_tool_extraction:** NO (request preprocessing only)

### Function: _build_tool_quick_reference
**Line:** 141
**Purpose:** Build quick reference table of available tools
**Signature:** `def _build_tool_quick_reference(tools: list[dict]) -> str`
**Status:** REQUEST_PREPROCESSING - Helper for build_tool_prompt
**Current Usage:** Internal helper for build_tool_prompt
**In universal_tool_extraction:** NO (request preprocessing only)

### Function: _build_few_shot_examples
**Line:** 202
**Purpose:** Build few-shot examples for tool usage
**Signature:** `def _build_few_shot_examples(tools: list[dict]) -> str`
**Status:** REQUEST_PREPROCESSING - Helper for build_tool_prompt
**Current Usage:** Internal helper for build_tool_prompt
**In universal_tool_extraction:** NO (request preprocessing only)

### Function: rewrite_messages_without_tools
**Line:** 336
**Purpose:** Rewrite messages to remove tool_use blocks (for no-tools models)
**Signature:** `def rewrite_messages_without_tools(messages: list[dict]) -> list[dict]`
**Status:** REQUEST_PREPROCESSING - Can be deleted
**Current Usage:**
- `converters.py` (imported and used)
**In universal_tool_extraction:** NO (request preprocessing only)

### Function: _merge_consecutive_messages
**Line:** 320
**Purpose:** Merge consecutive assistant messages (for message rewriting)
**Signature:** `def _merge_consecutive_messages(messages: list[dict]) -> list[dict]`
**Status:** REQUEST_PREPROCESSING - Helper for rewrite_messages_without_tools
**Current Usage:** Internal helper for rewrite_messages_without_tools
**In universal_tool_extraction:** NO (request preprocessing only)

---

## Section 3: Utility/Helper Functions (Needed)

These functions are general-purpose utilities needed by multiple modules.

### Function: _normalize_tool_name
**Line:** 39
**Purpose:** Normalize legacy tool names (e.g., "Task" → "Agent")
**Signature:** `def _normalize_tool_name(name: str) -> str`
**Status:** ESSENTIAL - Tool name normalization
**Current Usage:**
- Internal helper for extract_tool_calls_from_text
- Used throughout extraction functions
**In universal_tool_extraction:** NO (used internally by imported functions)

### Function: _build_valid_tool_names
**Line:** 46
**Purpose:** Extract set of valid tool names from request tools
**Signature:** `def _build_valid_tool_names(tools: list | None) -> set[str]`
**Status:** ESSENTIAL - Validation helper
**Current Usage:**
- `universal_tool_extraction.py` (imported and used)
- `converters.py` (imported and used)
- `streaming.py` (imported and used)
- Internal helper for validate_tool_name
**In universal_tool_extraction:** YES (imported from tool_prompting)

### Function: validate_tool_name
**Line:** 58
**Purpose:** Check if tool name is in allowlist (validation)
**Signature:** `def validate_tool_name(name: str, valid_names: set[str]) -> bool`
**Status:** ESSENTIAL - Tool name validation
**Current Usage:**
- `universal_tool_extraction.py` (imported and used)
- `converters.py` (imported and used)
- `streaming.py` (imported and used)
- `converters.py` (imported and used)
**In universal_tool_extraction:** YES (imported from tool_prompting)

### Function: is_no_tools_model
**Line:** 88
**Purpose:** Check if model requires no-tools mode
**Signature:** `def is_no_tools_model(model: str) -> bool`
**Status:** REQUEST_PREPROCESSING - Model classification
**Current Usage:**
- `converters.py` (imported and used)
- `streaming.py` (imported and used)
- `transformers/compression.py` (imported and used)
**In universal_tool_extraction:** NO (request preprocessing only)

### Function: _load_no_tools_models
**Line:** 72
**Purpose:** Load NO_TOOLS_MODELS from environment
**Signature:** `def _load_no_tools_models() -> FrozenSet[str]`
**Status:** REQUEST_PREPROCESSING - Helper for is_no_tools_model
**Current Usage:** Internal helper for is_no_tools_model
**In universal_tool_extraction:** NO (request preprocessing only)

### Function: _type_compatible
**Line:** 829
**Purpose:** Check if value type is compatible with schema type
**Signature:** `def _type_compatible(value: Any, schema_type: str) -> bool`
**Status:** ESSENTIAL - Type checking helper
**Current Usage:** Internal helper for _repair_tool_input
**In universal_tool_extraction:** NO (used internally by imported functions)

### Function: _get_tool_schema
**Line:** 928
**Purpose:** Get tool schema by name
**Signature:** `def _get_tool_schema(tool_name: str, tools: list | None) -> dict | None`
**Status:** ESSENTIAL - Schema access helper
**Current Usage:** Internal helper for _repair_tool_input, _get_tool_required_fields, _get_tool_properties
**In universal_tool_extraction:** NO (used internally by imported functions)

### Function: _get_tool_required_fields
**Line:** 940
**Purpose:** Get required fields from tool schema
**Signature:** `def _get_tool_required_fields(tool_name: str, tools: list | None) -> set[str]`
**Status:** ESSENTIAL - Schema access helper
**Current Usage:**
- Internal helper for _repair_tool_input, recover_truncated_deterministic
**In universal_tool_extraction:** NO (used internally by imported functions)

### Function: _get_tool_properties
**Line:** 948
**Purpose:** Get properties from tool schema
**Signature:** `def _get_tool_properties(tool_name: str, tools: list | None) -> dict`
**Status:** ESSENTIAL - Schema access helper
**Current Usage:** Internal helper for _repair_tool_input
**In universal_tool_extraction:** NO (used internally by imported functions)

### Function: _strip_inner_xml_tags (already listed above)
**Line:** 473
**Status:** ESSENTIAL - Also a utility function
**Note:** Listed in Section 1 but also serves as utility

---

## Section 4: Classes/Structures

### Class: XmlToolBuffer
**Line:** 1238
**Purpose:** State machine for detecting tool_call XML tags in streaming text
**Status:** ESSENTIAL - Streaming buffer class
**Current Usage:**
- `streaming.py` (imported and used)
- `universal_tool_extraction.py` has its own _XmlToolBuffer class (migrated)
**In universal_tool_extraction:** YES (migrated as _XmlToolBuffer class)

**Migration Status:**
- `universal_tool_extraction.py` has its own `_XmlToolBuffer` class (line 79)
- This is a clean migration, not an import
- Both classes have identical functionality

---

## Section 5: Regex Patterns and Constants

### Regex Patterns (Imported by universal_tool_extraction)
- `_TOOL_CALL_OPEN = "<tool_call"` (line 1213)
- `_REAL_NAME_RE = re.compile(r'<tool_call\s+name=["\'][^"\']+["\']')` (line 1217)
- `_TOOL_CALL_RE = re.compile(...)` (line 394)
- `_TOOL_CALL_FALLBACK_RE = re.compile(...)` (line 405)
- `_TOOL_CALL_BARE_RE = re.compile(...)` (line 411)
- `_TOOL_CALL_ARGKV_RE = re.compile(...)` (line 420)
- `_TOOL_CALL_ARGKV_LOOSE_RE = re.compile(...)` (line 427)
- `_TOOL_CALL_GREEDY_RE = re.compile(...)` (line 400)

**Status:** ESSENTIAL - Used by extraction functions
**In universal_tool_extraction:** YES (imported from tool_prompting)

### Regex Patterns (Not imported by universal_tool_extraction)
- `_TOOL_DILUTED_RE = re.compile(...)` (line 442)
- `_XML_PARAM_TAG_RE = re.compile(r'<(\w+)>([\s\S]*?)</\1>', re.DOTALL)` (line 460)
- `_XML_ATTR_PARAM_RE = re.compile(...)` (line 464)
- `_CDATA_RE = re.compile(r'^<!\[CDATA\[([\s\S]*?)\]\]>$')` (line 470)
- `_ARG_KV_PAIR_RE = re.compile(...)` (line 431)
- `_PARTIAL_TOOL_RE = re.compile(...)` (line 910)
- `_PARTIAL_ARGKV_RE = re.compile(...)` (line 916)
- `_PARTIAL_XML_TAGS_RE = re.compile(...)` (line 922)
- `_TOOL_NAME_ALIASES: dict[str, str] = {"Task": "Agent"}` (line 34)
- `_NAME_ATTR = r'name=["\']([^"\']+)["\']'` (defined in comments)

**Status:** ESSENTIAL - Used internally by imported functions
**In universal_tool_extraction:** NO (used internally by imported functions)

---

## Section 6: Gap Analysis

### What's in tool_prompting that universal_tool_extraction DOESN'T have

#### 1. Request Preprocessing Functions (NEVER needed by universal_tool_extraction)
- `build_tool_prompt()` - Request preprocessing only
- `_format_schema_properties()` - Request preprocessing only
- `_build_tool_quick_reference()` - Request preprocessing only
- `_build_few_shot_examples()` - Request preprocessing only
- `rewrite_messages_without_tools()` - Request preprocessing only
- `_merge_consecutive_messages()` - Request preprocessing only

**Conclusion:** These 6 functions can be safely deleted after migration.

#### 2. Recovery Functions (Used by streaming/converters but not universal_tool_extraction)
- `recover_truncated_deterministic()` - Needed by streaming and converters
- `recover_incomplete_tool_call()` - Needed by streaming only

**Conclusion:** These need to be moved to a recovery module or kept in tool_prompting for streaming support.

#### 3. Internal Helper Functions (Used by imported functions)
- `_normalize_tool_name()` - Used by extract_tool_calls_from_text
- `_safe_parse_tool_input()` - Used by extract_tool_calls_from_text
- `_greedy_extract_json_fields()` - Used by _safe_parse_tool_input
- `_schema_aware_cleanup()` - Used by _safe_parse_tool_input
- `_repair_tool_input()` - Used by _safe_parse_tool_input
- `_type_compatible()` - Used by _repair_tool_input
- `_get_tool_schema()` - Used by schema helpers
- `_get_tool_required_fields()` - Used by schema helpers
- `_get_tool_properties()` - Used by schema helpers
- `_strip_inner_xml_tags()` - Used by _safe_parse_tool_input
- `_parse_argkv_tool()` - Imported by universal_tool_extraction

**Conclusion:** These are internal dependencies of the imported functions. They don't need to be in universal_tool_extraction.

#### 4. Model Classification Functions (Request preprocessing)
- `is_no_tools_model()` - Request preprocessing only
- `_load_no_tools_models()` - Request preprocessing only

**Conclusion:** These can be deleted after migration.

---

## Section 7: Migration Strategy

### Phase 1: Identify Shared Dependencies
**Functions needed by multiple modules:**
- `extract_tool_calls_from_text()` - Used by 4 modules
- `_build_valid_tool_names()` - Used by 3 modules
- `validate_tool_name()` - Used by 3 modules
- `strip_tool_call_xml()` - Used by 4 modules
- `recover_truncated_deterministic()` - Used by 2 modules
- `recover_incomplete_tool_call()` - Used by 1 module (streaming)

**Recommendation:** Move these to `utils/tool_utils.py` or create `llm/tool_extraction.py`

### Phase 2: Migrate to utils/tool_utils.py
**Core extraction functions to migrate:**
1. `extract_tool_calls_from_text()`
2. `_build_valid_tool_names()`
3. `validate_tool_name()`
4. `strip_tool_call_xml()`
5. `_parse_xml_as_tags()`
6. `_parse_argkv_tool()`
7. `_normalize_escaped_xml()`

**Internal dependencies (move with core functions):**
1. `_normalize_tool_name()`
2. `_safe_parse_tool_input()`
3. `_strip_inner_xml_tags()`
4. `_greedy_extract_json_fields()`
5. `_schema_aware_cleanup()`
6. `_repair_tool_input()`
7. `_type_compatible()`
8. `_get_tool_schema()`
9. `_get_tool_required_fields()`
10. `_get_tool_properties()`

**Regex patterns to migrate:**
- All `_TOOL_CALL_*` patterns
- `_XML_PARAM_TAG_RE`, `_XML_ATTR_PARAM_RE`, `_CDATA_RE`
- `_ARG_KV_PAIR_RE`, partial tool regexes
- `_TOOL_NAME_ALIASES`

### Phase 3: Create llm/tool_recovery.py
**Recovery functions to migrate:**
1. `recover_truncated_deterministic()`
2. `recover_incomplete_tool_call()`

### Phase 4: Delete tool_prompting.py
**Functions that can be deleted immediately:**
1. `build_tool_prompt()` - Request preprocessing
2. `_format_schema_properties()` - Request preprocessing
3. `_build_tool_quick_reference()` - Request preprocessing
4. `_build_few_shot_examples()` - Request preprocessing
5. `rewrite_messages_without_tools()` - Request preprocessing
6. `_merge_consecutive_messages()` - Request preprocessing
7. `is_no_tools_model()` - Request preprocessing
8. `_load_no_tools_models()` - Request preprocessing
9. `XmlToolBuffer` - Migrated to universal_tool_extraction.py

---

## Section 8: Import Dependency Matrix

### universal_tool_extraction.py imports from tool_prompting:
```
from llm.tool_prompting import (
    extract_tool_calls_from_text,      # Core extraction
    _parse_xml_as_tags,                # XML parsing
    strip_tool_call_xml,               # Cleanup
    _build_valid_tool_names,           # Validation
    validate_tool_name,                # Validation
    _parse_argkv_tool,                 # GLM parsing
    _normalize_escaped_xml,            # XML unescaping
    _TOOL_CALL_OPEN,                   # Constant
    _TOOL_CALL_RE,                     # Regex
    _TOOL_CALL_FALLBACK_RE,            # Regex
    _TOOL_CALL_GREEDY_RE,              # Regex
    _REAL_NAME_RE,                     # Regex
    _TOOL_CALL_ARGKV_LOOSE_RE,         # Regex
)
```

### streaming.py imports from tool_prompting:
```
from llm.tool_prompting import (
    is_no_tools_model,                 # Model classification (DELETE)
    XmlToolBuffer,                     # Migrated to universal_tool_extraction
    recover_incomplete_tool_call,      # Recovery function (KEEP)
    extract_tool_calls_from_text,      # Core extraction (MOVE)
    strip_tool_call_xml,               # Cleanup (MOVE)
    _build_valid_tool_names,           # Validation (MOVE)
    validate_tool_name,                # Validation (MOVE)
)
```

### converters.py imports from tool_prompting:
```
from llm.tool_prompting import (
    is_no_tools_model,                 # Model classification (DELETE)
    build_tool_prompt,                 # Request preprocessing (DELETE)
    rewrite_messages_without_tools,    # Request preprocessing (DELETE)
    extract_tool_calls_from_text,      # Core extraction (MOVE)
    strip_tool_call_xml,               # Cleanup (MOVE)
    recover_truncated_deterministic,   # Recovery (MOVE)
    _build_valid_tool_names,           # Validation (MOVE)
    validate_tool_name,                # Validation (MOVE)
)
```

### reasoning_handling.py imports from tool_prompting:
```
from llm.tool_prompting import (
    extract_tool_calls_from_text,      # Core extraction (MOVE)
    _parse_xml_as_tags,                # XML parsing (MOVE)
    strip_tool_call_xml,               # Cleanup (MOVE)
)
```

### transformers/compression.py imports from tool_prompting:
```
from llm.tool_prompting import (
    is_no_tools_model,                 # Model classification (DELETE)
)
```

---

## Section 9: Final Classification Summary

### ESSENTIAL - Extraction Functions (12)
1. extract_tool_calls_from_text
2. _safe_parse_tool_input
3. _strip_inner_xml_tags
4. _parse_xml_as_tags
5. _greedy_extract_json_fields
6. _schema_aware_cleanup
7. _parse_argkv_tool
8. strip_tool_call_xml
9. _normalize_escaped_xml
10. recover_truncated_deterministic
11. recover_incomplete_tool_call
12. _repair_tool_input

### REQUEST_PREPROCESSING - Delete After Migration (6)
1. build_tool_prompt
2. _format_schema_properties
3. _build_tool_quick_reference
4. _build_few_shot_examples
5. rewrite_messages_without_tools
6. _merge_consecutive_messages

### REQUEST_PREPROCESSING - Model Classification (2)
1. is_no_tools_model
2. _load_no_tools_models

### ESSENTIAL - Utility/Helper Functions (10)
1. _normalize_tool_name
2. _build_valid_tool_names
3. validate_tool_name
4. _type_compatible
5. _get_tool_schema
6. _get_tool_required_fields
7. _get_tool_properties
8. _strip_inner_xml_tags (also extraction)

### ESSENTIAL - Classes (1)
1. XmlToolBuffer (migrated to universal_tool_extraction as _XmlToolBuffer)

### ESSENTIAL - Regex Patterns (15+)
- All _TOOL_CALL_* patterns
- All XML parsing patterns
- All recovery patterns

---

## Section 10: Recommended File Structure After Migration

```
vendor/claude-code-proxy/
├── llm/
│   ├── tool_extraction.py          # NEW: Core extraction functions
│   │   ├── extract_tool_calls_from_text()
│   │   ├── _safe_parse_tool_input()
│   │   ├── _parse_xml_as_tags()
│   │   ├── _parse_argkv_tool()
│   │   ├── strip_tool_call_xml()
│   │   ├── _normalize_escaped_xml()
│   │   ├── _normalize_tool_name()
│   │   ├── _build_valid_tool_names()
│   │   ├── validate_tool_name()
│   │   ├── _repair_tool_input()
│   │   ├── Schema helpers
│   │   └── All regex patterns
│   │
│   ├── tool_recovery.py            # NEW: Recovery functions
│   │   ├── recover_truncated_deterministic()
│   │   └── recover_incomplete_tool_call()
│   │
│   ├── transformers/
│   │   └── universal_tool_extraction.py  # EXISTING: Use tool_extraction.py
│   │
│   ├── converters.py               # UPDATE: Import from tool_extraction.py
│   ├── streaming.py                # UPDATE: Import from tool_extraction.py
│   └── tool_prompting.py            # DELETE: After migration complete
```

---

## Section 11: Action Items

### Immediate Actions (Phase 1):
1. ✅ Create `utils/tool_extraction.py` or `llm/tool_extraction.py`
2. ✅ Move core extraction functions (Section 1, except recovery)
3. ✅ Update imports in universal_tool_extraction.py
4. ✅ Update imports in reasoning_handling.py

### Medium Actions (Phase 2):
5. ⏳ Create `llm/tool_recovery.py`
6. ⏳ Move recovery functions (recover_truncated_deterministic, recover_incomplete_tool_call)
7. ⏳ Update imports in streaming.py and converters.py
8. ⏳ Delete request preprocessing functions from tool_prompting.py

### Final Actions (Phase 3):
9. ⏳ Delete tool_prompting.py
10. ⏳ Update all remaining imports
11. ⏳ Run tests to verify functionality
12. ⏳ Update documentation

---

## Section 12: Testing Checklist

Before deleting tool_prompting.py, verify:

- [ ] universal_tool_extraction.py tests pass
- [ ] streaming.py tests pass
- [ ] converters.py tests pass
- [ ] reasoning_handling.py tests pass
- [ ] compression.py tests pass (if is_no_tools_model is used)
- [ ] All regex patterns work correctly in new location
- [ ] Recovery functions work correctly in new location
- [ ] No circular imports
- [ ] All imports updated across codebase

---

**End of Catalog**
