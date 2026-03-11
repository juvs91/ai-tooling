# Comprehensive Gap Analysis: tool_prompting.py vs universal_tool_extraction.py

## Executive Summary

**tool_prompting.py**: 1,670 lines (DEPRECATED - will be deleted after migration)
**universal_tool_extraction.py**: 1,009 lines

universal_tool_extraction.py imports 8 functions and 5 regex patterns from tool_prompting.py, creating a dependency chain that prevents DRY deletion. Several functions are misnamed or have incorrect signatures.

---

## 1. Extraction Functions Gap Analysis

### Functions in tool_prompting.py (20 functions):
1. `_normalize_tool_name(name: str) -> str` (line 39)
2. `_build_valid_tool_names(tools: list | None) -> set[str]` (line 46)
3. `validate_tool_name(name: str, valid_names: set[str]) -> bool` (line 58)
4. `_load_no_tools_models() -> FrozenSet[str]` (line 72)
5. `is_no_tools_model(model: str) -> bool` (line 88)
6. `_format_schema_properties(input_schema: dict, depth: int = 0, max_depth: int = 2) -> str` (line 101)
7. `_build_tool_quick_reference(tools: list[dict]) -> str` (line 141)
8. `_build_few_shot_examples(tools: list[dict]) -> str` (line 202)
9. `build_tool_prompt(tools: list[dict]) -> str` (line 249)
10. `_merge_consecutive_messages(messages: list[dict]) -> list[dict]` (line 320)
11. `rewrite_messages_without_tools(messages: list[dict]) -> list[dict]` (line 336)
12. `_parse_argkv_tool(match) -> dict` (line 448)
13. `_strip_inner_xml_tags(raw: str) -> str` (line 473)
14. `_parse_xml_as_tags(raw: str, tool_name: str, tools: list | None = None) -> dict | None` (line 501)
15. `_greedy_extract_json_fields(raw: str, tool_name: str, tools: list | None) -> dict | None` (line 552)
16. `_schema_aware_cleanup(parsed: dict, tool_name: str, tools: list | None) -> dict` (line 611)
17. `_safe_parse_tool_input(raw_input: str, tool_name: str, tools: list | None = None) -> dict` (line 639)
18. `extract_tool_calls_from_text(text: str, valid_tool_names: set[str] | None = None, tools: list | None = None) -> tuple[list[dict], str]` (line 695)
19. `_type_compatible(value: Any, schema_type: str) -> bool` (line 829)
20. `_repair_tool_input(name: str, input_dict: dict, tools: list | None) -> dict` (line 845)
21. `_get_tool_schema(tool_name: str, tools: list | None) -> dict | None` (line 928)
22. `_get_tool_required_fields(tool_name: str, tools: list | None) -> set[str]` (line 940)
23. `_get_tool_properties(tool_name: str, tools: list | None) -> dict` (line 948)
24. `recover_truncated_deterministic(partial_xml: str, tools: list | None = None) -> list[dict] | None` (line 956)
25. `strip_tool_call_xml(text: str) -> str` (line 1180)
26. `recover_incomplete_tool_call(...)` (line 1084) - async LLM recovery
27. `_normalize_escaped_xml(xml: str) -> str | None` (line 1220)

### Functions in universal_tool_extraction.py (3 unique functions):
1. `_ensure_request_object(request: Any) -> Any` (line 39)
2. `_XmlToolBuffer` class (line 79) - simplified version of tool_prompting's XmlToolBuffer
3. `UniversalToolExtractionTransformer` class (line 518)

### Functions Imported from tool_prompting.py (8 functions):
1. `extract_tool_calls_from_text` - main extraction function
2. `_parse_xml_as_tags` - XML tag parser
3. `strip_tool_call_xml` - XML stripper
4. `_build_valid_tool_names` - tool name validation
5. `validate_tool_name` - name validator
6. `_parse_argkv_tool` - GLM argkv parser
7. `_normalize_escaped_xml` - XML unescape
8. `_TOOL_CALL_OPEN`, `_TOOL_CALL_RE`, `_TOOL_CALL_FALLBACK_RE`, `_TOOL_CALL_GREEDY_RE`, `_REAL_NAME_RE`, `_TOOL_CALL_ARGKV_LOOSE_RE` (6 regex patterns)

### **GAP: Functions NOT in universal_tool_extraction.py but NEEDED:**

These functions are CALLED by universal_tool_extraction but must be migrated from tool_prompting:

1. **`_safe_parse_tool_input(raw_input: str, tool_name: str, tools: list | None = None) -> dict`**
   - Used by: `extract_tool_calls_from_text` (imported from tool_prompting)
   - Purpose: Parse tool input JSON with multiple fallback strategies
   - Depends on: `_strip_inner_xml_tags`, `_parse_xml_as_tags`, `_greedy_extract_json_fields`, `_schema_aware_cleanup`

2. **`_get_tool_schema(tool_name: str, tools: list | None) -> dict | None`**
   - Used by: `_parse_xml_as_tags` (imported from tool_prompting)
   - Purpose: Get a tool's input_schema by name

3. **`_get_tool_required_fields(tool_name: str, tools: list | None) -> set[str]`**
   - Used by: `_parse_xml_as_tags` (imported from tool_prompting)
   - Purpose: Get required fields from a tool's input_schema

4. **`_get_tool_properties(tool_name: str, tools: list | None) -> dict`**
   - Used by: `_parse_xml_as_tags` (imported from tool_prompting)
   - Purpose: Get properties dict from a tool's input_schema

5. **`_strip_inner_xml_tags(raw: str) -> str`**
   - Used by: `_safe_parse_tool_input` (indirectly via extract_tool_calls_from_text)
   - Purpose: Strip wrapping XML inner tags

6. **`_greedy_extract_json_fields(raw: str, tool_name: str, tools: list | None) -> dict | None`**
   - Used by: `_safe_parse_tool_input` (indirectly via extract_tool_calls_from_text)
   - Purpose: Greedy field extraction for tools with large string content

7. **`_schema_aware_cleanup(parsed: dict, tool_name: str, tools: list | None) -> dict`**
   - Used by: `_safe_parse_tool_input` (indirectly via extract_tool_calls_from_text)
   - Purpose: Filter parsed dict to only include keys from the tool's schema

---

## 2. Regex Patterns Gap Analysis

### Regex Patterns in tool_prompting.py (16 patterns):

1. `_TOOL_NAME_ALIASES` (line 34) - dict, not regex
2. `_INNER_TAG = r"(?:input|textarea|arguments|params|json|content|parameters)"` (line 390)
3. `_NAME_ATTR = r"""name=["']([^"']+)["']"""` (line 391)
4. `_REASONING_SKIP = r'(?:<reasoning>[\s\S]*?</reasoning>\s*)*'` (line 393)
5. `_TOOL_CALL_RE = re.compile(...)` (line 394) - IMPORTED
6. `_TOOL_CALL_GREEDY_RE = re.compile(...)` (line 400) - IMPORTED
7. `_TOOL_CALL_FALLBACK_RE = re.compile(...)` (line 405) - IMPORTED
8. `_TOOL_CALL_BARE_RE = re.compile(...)` (line 411) - NOT IMPORTED
9. `_TOOL_CALL_ARGKV_RE = re.compile(...)` (line 420) - NOT IMPORTED
10. `_TOOL_CALL_ARGKV_LOOSE_RE = re.compile(...)` (line 427) - IMPORTED
11. `_ARG_KV_PAIR_RE = re.compile(...)` (line 431) - NOT IMPORTED
12. `_TOOL_DILUTED_RE = re.compile(...)` (line 442) - NOT IMPORTED
13. `_XML_PARAM_TAG_RE = re.compile(r'<(\w+)>([\s\S]*?)</\1>', re.DOTALL)` (line 460) - NOT IMPORTED
14. `_XML_ATTR_PARAM_RE = re.compile(...)` (line 464) - NOT IMPORTED
15. `_CDATA_RE = re.compile(r'^<!\[CDATA\[([\s\S]*?)\]\]>$')` (line 470) - NOT IMPORTED
16. `_PARTIAL_TOOL_RE = re.compile(...)` (line 910) - NOT IMPORTED
17. `_PARTIAL_ARGKV_RE = re.compile(...)` (line 916) - NOT IMPORTED
18. `_PARTIAL_XML_TAGS_RE = re.compile(...)` (line 922) - NOT IMPORTED
19. `_TOOL_CALL_OPEN = "<tool_call"` (line 1213) - IMPORTED
20. `_REAL_NAME_RE = re.compile(...)` (line 1217) - IMPORTED

### Regex Patterns in universal_tool_extraction.py (2 patterns):

1. `_TOOL_CALL_CLOSE = "</think>"` (line 76) - duplicate of _TOOL_CALL_OPEN but for closing tag
2. Inline regex in `_format_tool_result`: `re.search(r'<textarea>(.*?)</textarea>', ...)` (line 450)

### **GAP: Regex Patterns NOT in universal_tool_extraction.py:**

1. **`_TOOL_NAME_ALIASES`** - Tool name normalization mapping (Task → Agent)
2. **`_INNER_TAG`** - Pattern for inner tag variants
3. **`_NAME_ATTR`** - Pattern for name attribute extraction
4. **`_REASONING_SKIP`** - Pattern to skip <reasoning> tags inside tool calls
5. **`_TOOL_CALL_BARE_RE`** - Regex for bare format (no inner tags)
6. **`_TOOL_CALL_ARGKV_RE`** - Regex for GLM argkv format
7. **`_ARG_KV_PAIR_RE`** - Regex for parsing argkv pairs
8. **`_TOOL_DILUTED_RE`** - Regex for diluted XML format (after prompt dilution)
9. **`_XML_PARAM_TAG_RE`** - Regex for XML-as-tags format
10. **`_XML_ATTR_PARAM_RE`** - Regex for attributed XML parameter format
11. **`_CDATA_RE`** - Regex for CDATA section detection
12. **`_PARTIAL_TOOL_RE`** - Regex for partial tool call extraction
13. **`_PARTIAL_ARGKV_RE`** - Regex for partial argkv extraction
14. **`_PARTIAL_XML_TAGS_RE`** - Regex for partial XML tags extraction

**Note**: These patterns are used by the imported functions from tool_prompting.py, so they must be available.

---

## 3. Utility Functions Gap Analysis

### Validation Functions:

1. **`_normalize_tool_name(name: str) -> str`** (line 39)
   - Status: NOT imported
   - Used by: `extract_tool_calls_from_text` (in tool_prompting)
   - Purpose: Normalize legacy tool names (Task → Agent)

2. **`validate_tool_name(name: str, valid_names: set[str]) -> bool`** (line 58)
   - Status: IMPORTED
   - Purpose: Check if tool name is in allowlist

3. **`_build_valid_tool_names(tools: list | None) -> set[str]`** (line 46)
   - Status: IMPORTED
   - Purpose: Extract set of valid tool names from request tools

### Schema Access Functions:

1. **`_get_tool_schema(tool_name: str, tools: list | None) -> dict | None`** (line 928)
   - Status: NOT imported
   - Used by: `_parse_xml_as_tags` (imported from tool_prompting)
   - Purpose: Get a tool's input_schema by name

2. **`_get_tool_required_fields(tool_name: str, tools: list | None) -> set[str]`** (line 940)
   - Status: NOT imported
   - Used by: `_parse_xml_as_tags` (imported from tool_prompting)
   - Purpose: Get required fields from a tool's input_schema

3. **`_get_tool_properties(tool_name: str, tools: list | None) -> dict`** (line 948)
   - Status: NOT imported
   - Used by: `_parse_xml_as_tags` (imported from tool_prompting)
   - Purpose: Get properties dict from a tool's input_schema

### Tool Input Parsing Functions:

1. **`_safe_parse_tool_input(raw_input: str, tool_name: str, tools: list | None = None) -> dict`** (line 639)
   - Status: NOT imported
   - Used by: `extract_tool_calls_from_text` (imported from tool_prompting)
   - Purpose: Parse tool input JSON with multiple fallback strategies

2. **`_strip_inner_xml_tags(raw: str) -> str`** (line 473)
   - Status: NOT imported
   - Used by: `_safe_parse_tool_input` (indirectly)
   - Purpose: Strip wrapping XML inner tags

3. **`_parse_xml_as_tags(raw: str, tool_name: str, tools: list | None = None) -> dict | None`** (line 501)
   - Status: IMPORTED (BUT MISUSED)
   - Purpose: Convert XML-as-tags format to JSON dict

4. **`_greedy_extract_json_fields(raw: str, tool_name: str, tools: list | None) -> dict | None`** (line 552)
   - Status: NOT imported
   - Used by: `_safe_parse_tool_input` (indirectly)
   - Purpose: Greedy field extraction for tools with large string content

5. **`_schema_aware_cleanup(parsed: dict, tool_name: str, tools: list | None) -> dict`** (line 611)
   - Status: NOT imported
   - Used by: `_safe_parse_tool_input` (indirectly)
   - Purpose: Filter parsed dict to only include keys from the tool's schema

### Recovery Functions:

1. **`recover_truncated_deterministic(partial_xml: str, tools: list | None = None) -> list[dict] | None`** (line 956)
   - Status: NOT imported
   - Purpose: Recover truncated tool calls deterministically (no LLM)

2. **`recover_incomplete_tool_call(...)` (line 1084)
   - Status: NOT imported
   - Purpose: LLM-based recovery for incomplete tool calls (async)

### Tool Prompt Building Functions (NOT needed for extraction):

These are NOT needed for universal_tool_extraction.py - they're for prompting:
- `_format_schema_properties`
- `_build_tool_quick_reference`
- `_build_few_shot_examples`
- `build_tool_prompt`

### Message Rewriting Functions (NOT needed for extraction):

These are NOT needed for universal_tool_extraction.py - they're for request rewriting:
- `_merge_consecutive_messages`
- `rewrite_messages_without_tools`

### Model Detection Functions (NOT needed for agnostic extraction):

These are NOT needed for universal_tool_extraction.py - they're model-specific:
- `_load_no_tools_models`
- `is_no_tools_model`

---

## 4. Bugs and Issues in universal_tool_extraction.py

### CRITICAL BUG #1: Undefined `ctx` variable in streaming methods

**Location**: Lines 884, 891
```python
def process_streaming_chunk(self, request: object, text_chunk: str) -> List[Dict]:
    # ...
    if not ctx.tools:  # ❌ ctx is not defined!
        logger.debug(f"[universal-tool-extraction] Skipping streaming - no tools in context")
        return []
    # ...
    tools = ctx.tools  # ❌ ctx is not defined!
```

**Issue**: The method signature doesn't include `ctx: TransformContext` parameter, but the code references `ctx.tools`.

**Fix Required**: Add `ctx: TransformContext` parameter to method signature:
```python
def process_streaming_chunk(self, request: object, text_chunk: str, ctx: TransformContext) -> List[Dict]:
```

### CRITICAL BUG #2: Incorrect usage of `_parse_xml_as_tags`

**Location**: Line 642
```python
xml_tags = _parse_xml_as_tags(clean_reasoning, "tool_call", tools=getattr(request, "tools", None))
```

**Issue**: `_parse_xml_as_tags` signature is:
```python
def _parse_xml_as_tags(raw: str, tool_name: str, tools: list | None = None) -> dict | None:
```

It expects `tool_name` to be the actual tool name (e.g., "Read", "Write"), NOT "tool_call".
The function returns a `dict | None` (parsed parameters), NOT a list of tool calls.

**Fix Required**: This function is being misused. It should be called by `_safe_parse_tool_input`, not directly here.

### CRITICAL BUG #3: Missing `_safe_parse_tool_input` dependency

**Issue**: The `_XmlToolBuffer._parse_tool_xml` method calls `_format_tool_result`, which tries to parse tool input, but it doesn't have access to `_safe_parse_tool_input` which handles all the fallback strategies.

**Current Code** (line 462-489):
```python
def _format_tool_result(self, match, label: str) -> Dict:
    tool_input_raw = match.group(2) if match.lastindex >= 2 else ""
    # Try JSON parsing first (standard format)
    if tool_input_raw:
        try:
            tool_input = json.loads(tool_input_raw)
        except json.JSONDecodeError:
            # Fallback: XML-escaped quotes or malformed JSON
            param_pattern = re.compile(r'<param name="([^"]+)">\s*(.*?)\s*</param>', re.DOTALL)
            params = param_pattern.findall(tool_input_raw)
            if params:
                tool_input = {name: value.strip() for name, value in params}
            else:
                # Last resort: treat as plain text
                tool_input = {"content": tool_input_raw.strip()}
```

**Problem**: This only handles 2 fallback strategies (JSON parsing, param tags), but the original `_safe_parse_tool_input` handles 7 fallback strategies:
1. Strip XML tags
2. XML-as-tags format
3. Direct JSON parse
4. json_repair
5. Greedy field extraction
6. Schema-aware cleanup
7. Wrap raw string

**Fix Required**: Either import `_safe_parse_tool_input` or reimplement the full fallback logic.

### CRITICAL BUG #4: Missing regex patterns used by imported functions

**Issue**: The following regex patterns are used by imported functions but are not defined in universal_tool_extraction.py:

1. `_TOOL_CALL_BARE_RE` - used by `extract_tool_calls_from_text`
2. `_TOOL_CALL_ARGKV_RE` - used by `extract_tool_calls_from_text`
3. `_TOOL_DILUTED_RE` - used by `extract_tool_calls_from_text`
4. `_XML_PARAM_TAG_RE` - used by `_parse_xml_as_tags`
5. `_XML_ATTR_PARAM_RE` - used by `_parse_xml_as_tags`
6. `_CDATA_RE` - used by `_strip_inner_xml_tags`
7. `_ARG_KV_PAIR_RE` - used by `_parse_argkv_tool`
8. `_INNER_TAG`, `_NAME_ATTR`, `_REASONING_SKIP` - used by various regex patterns

**Current Status**: These are defined in tool_prompting.py and are accessible via the import chain, but this creates a hard dependency.

**Fix Required**: Either define these patterns in universal_tool_extraction.py or ensure they're exported from a shared module.

---

## 5. XmlToolBuffer vs _XmlToolBuffer Comparison

### Original XmlToolBuffer (tool_prompting.py, line 1238):

**Methods**:
- `__init__(valid_tool_names, tools)`
- `feed(text) -> List[Dict]`
- `_has_plausible_tool_call() -> bool`
- `flush() -> List[Dict]`
- `_drain() -> List[Dict]`
- `_try_extract_text() -> Dict | None`
- `_try_extract_tool() -> Dict | None`
- `_is_backtick_quoted(idx) -> bool`
- `_parse_tool_xml(xml) -> Dict`
- `_safe_text_end() -> int`

**Key Features**:
- Uses `_safe_parse_tool_input` for robust input parsing (7 fallback strategies)
- Handles all 4 primary regex formats (PRIMARY, FALLBACK, BARE, ARGKV)
- Implements greedy regex for nested XML in content
- Supports escaped XML normalization
- Validates tool names against allowlist
- Comprehensive error handling and logging

### Migrated _XmlToolBuffer (universal_tool_extraction.py, line 79):

**Methods**:
- `__init__(valid_tool_names, tools)`
- `feed(text) -> List[Dict]`
- `_has_plausible_tool_call() -> bool`
- `flush() -> List[Dict]`
- `_drain() -> List[Dict]`
- `_try_extract_text() -> Dict | None`
- `_try_extract_tool() -> Dict | None`
- `_is_backtick_quoted(idx) -> bool`
- `_parse_tool_xml(xml) -> Dict`
- `_safe_text_end() -> int`
- `_format_tool_result(match, label) -> Dict`

**Missing Features**:
- ❌ No `_safe_parse_tool_input` support - only 2 fallback strategies instead of 7
- ❌ No BARE regex support
- ❌ No ARGKV regex support (only ARGKV_LOOSE)
- ❌ No escaped XML normalization in _parse_tool_xml
- ❌ No tool name validation in _parse_tool_xml
- ❌ Simplified _format_tool_result doesn't use schema-aware cleanup

**Code Differences**:

1. **_parse_tool_xml method**:
   - Original: 95 lines (1568-1663), handles PRIMARY, FALLBACK, BARE, ARGKV with validation
   - Migrated: 62 lines (399-460), only handles PRIMARY, FALLBACK, and hardcoded textarea

2. **_format_tool_result method**:
   - Original: Uses `_safe_parse_tool_input` for robust parsing
   - Migrated: Simple JSON parsing with 2 basic fallbacks

---

## 6. What Needs to be Added to universal_tool_extraction.py

### Priority 1: CRITICAL (Breaking bugs)

1. **Add `ctx: TransformContext` parameter to streaming methods**
   - `process_streaming_chunk(self, request, text_chunk, ctx: TransformContext)`
   - `flush_streaming_buffer(self, request, ctx: TransformContext)`

2. **Fix `_parse_xml_as_tags` usage in `_extract_tools_from_reasoning`**
   - Current: `xml_tags = _parse_xml_as_tags(clean_reasoning, "tool_call", ...)`
   - Should use `extract_tool_calls_from_text` instead, or remove reasoning extraction entirely

3. **Add missing regex patterns or import them properly**
   - `_TOOL_CALL_BARE_RE`
   - `_TOOL_CALL_ARGKV_RE`
   - `_TOOL_DILUTED_RE`
   - `_XML_PARAM_TAG_RE`
   - `_XML_ATTR_PARAM_RE`
   - `_CDATA_RE`
   - `_ARG_KV_PAIR_RE`
   - `_INNER_TAG`, `_NAME_ATTR`, `_REASONING_SKIP`

### Priority 2: HIGH (Feature parity)

4. **Migrate `_safe_parse_tool_input` function**
   - Lines 639-692 in tool_prompting.py
   - Depends on: `_strip_inner_xml_tags`, `_parse_xml_as_tags`, `_greedy_extract_json_fields`, `_schema_aware_cleanup`

5. **Migrate schema access functions**
   - `_get_tool_schema` (line 928)
   - `_get_tool_required_fields` (line 940)
   - `_get_tool_properties` (line 948)

6. **Migrate XML parsing support functions**
   - `_strip_inner_xml_tags` (line 473)
   - `_greedy_extract_json_fields` (line 552)
   - `_schema_aware_cleanup` (line 611)

7. **Enhance _XmlToolBuffer._parse_tool_xml**
   - Add BARE regex support
   - Add ARGKV regex support
   - Add tool name validation
   - Add escaped XML normalization
   - Use `_safe_parse_tool_input` instead of simplified `_format_tool_result`

### Priority 3: MEDIUM (Completeness)

8. **Migrate tool name normalization**
   - `_normalize_tool_name` (line 39)
   - `_TOOL_NAME_ALIASES` dict (line 34)

9. **Migrate recovery functions**
   - `recover_truncated_deterministic` (line 956)
   - `recover_incomplete_tool_call` (line 1084) - async LLM recovery

10. **Add remaining regex patterns**
    - `_PARTIAL_TOOL_RE` (line 910)
    - `_PARTIAL_ARGKV_RE` (line 916)
    - `_PARTIAL_XML_TAGS_RE` (line 922)

### Priority 4: LOW (Optional optimizations)

11. **Remove duplicate code**
    - `_TOOL_CALL_CLOSE` duplicates _TOOL_CALL_OPEN's closing tag logic
    - Inline textarea regex duplicates existing patterns

12. **Consolidate imports**
    - All imports from tool_prompting should be removed after migration
    - Move shared patterns to a constants module

---

## 7. What Can Be Removed from universal_tool_extraction.py

### Duplicated/Redundant Code:

1. **`_TOOL_CALL_CLOSE = "</think>"`** (line 76)
   - This is just the closing tag string, can be derived from context
   - Original tool_prompting.py doesn't have this constant

2. **Inline textarea regex** (line 450)
   - `re.search(r'<textarea>(.*?)</textarea>', ...)`
   - This is handled by the PRIMARY regex's _INNER_TAG pattern

3. **Simplified `_format_tool_result` method**
   - Should be removed and replaced with calls to `_safe_parse_tool_input`
   - Current implementation is too basic and missing fallback strategies

---

## 8. Migration Strategy

### Phase 1: Fix Critical Bugs (Immediate)

1. Add `ctx: TransformContext` parameter to streaming methods
2. Fix `_parse_xml_as_tags` misusage in reasoning extraction
3. Ensure all required regex patterns are available

### Phase 2: Migrate Core Functions (High Priority)

4. Migrate `_safe_parse_tool_input` and all its dependencies
5. Migrate schema access functions (_get_tool_schema, etc.)
6. Migrate XML parsing support functions
7. Enhance _XmlToolBuffer to use migrated functions

### Phase 3: Complete Migration (Medium Priority)

8. Migrate tool name normalization
9. Migrate recovery functions
10. Add remaining regex patterns

### Phase 4: Cleanup (Low Priority)

11. Remove all imports from tool_prompting
12. Remove duplicated code
13. Delete tool_prompting.py after verification

---

## 9. Summary Statistics

| Metric | tool_prompting.py | universal_tool_extraction.py | Gap |
|--------|------------------|----------------------------|-----|
| Total Lines | 1,670 | 1,009 | -661 lines |
| Functions | 27 | 3 unique + 8 imported | -16 functions |
| Classes | 1 (XmlToolBuffer) | 2 (_XmlToolBuffer, Transformer) | +1 class |
| Regex Patterns | 16 | 2 + 6 imported | -8 patterns |
| Critical Bugs | N/A | 4 | +4 bugs |
| Missing Dependencies | N/A | 7 functions + 8 patterns | -15 items |

### Functions That MUST Be Migrated (7):

1. `_safe_parse_tool_input`
2. `_get_tool_schema`
3. `_get_tool_required_fields`
4. `_get_tool_properties`
5. `_strip_inner_xml_tags`
6. `_greedy_extract_json_fields`
7. `_schema_aware_cleanup`

### Regex Patterns That MUST Be Migrated (8):

1. `_TOOL_CALL_BARE_RE`
2. `_TOOL_CALL_ARGKV_RE`
3. `_TOOL_DILUTED_RE`
4. `_XML_PARAM_TAG_RE`
5. `_XML_ATTR_PARAM_RE`
6. `_CDATA_RE`
7. `_ARG_KV_PAIR_RE`
8. `_INNER_TAG`, `_NAME_ATTR`, `_REASONING_SKIP`

### Bugs That MUST Be Fixed (4):

1. Undefined `ctx` variable in streaming methods
2. Incorrect usage of `_parse_xml_as_tags`
3. Missing `_safe_parse_tool_input` in _XmlToolBuffer
4. Missing regex patterns

---

## 10. Recommended Action Plan

### Immediate (Today):

1. **Fix the `ctx` bug** in `process_streaming_chunk` and `flush_streaming_buffer`
2. **Fix the `_parse_xml_as_tags` misusage** in `_extract_tools_from_reasoning`
3. **Verify all regex patterns are accessible** (they work via imports but this is fragile)

### Short-term (This Week):

4. **Migrate `_safe_parse_tool_input` and dependencies** to enable robust tool input parsing
5. **Migrate schema access functions** to break dependency on tool_prompting
6. **Enhance _XmlToolBuffer** to use migrated functions instead of simplified logic

### Medium-term (Next Week):

7. **Migrate remaining utility functions** (name normalization, recovery)
8. **Add remaining regex patterns** for completeness
9. **Test thoroughly** across all model types

### Long-term (Next Sprint):

10. **Remove all imports from tool_prompting**
11. **Delete tool_prompting.py** after verification
12. **Update documentation** and deprecation notices

---

## Conclusion

universal_tool_extraction.py is **NOT READY** to stand alone as a DRY replacement for tool_prompting.py. It has:

- **4 critical bugs** that will cause runtime failures
- **7 missing functions** that are required by imported functions
- **8 missing regex patterns** that are required by imported functions
- **Simplified implementation** of _XmlToolBuffer that lacks robust error handling

The current implementation creates a **hard dependency on tool_prompting.py** via 8 function imports and 6 regex pattern imports. This prevents the DRY deletion of tool_prompting.py.

**Recommended Approach**: Fix the 4 critical bugs immediately, then migrate the 7 missing functions and 8 regex patterns in priority order to enable a clean separation of concerns.
