"""
Tool Extraction Regex Patterns

Common regex patterns for tool extraction from various model outputs.
These patterns are used across multiple transformers and converters.
"""
import re
from typing import FrozenSet
import os


# ---------------------------------------------------------------------------
# Regex patterns for tool extraction
# ---------------------------------------------------------------------------

# Pattern for inner XML tags (input, textarea, etc.)
_INNER_TAG = r"(?:input|textarea|arguments|params|json|content|parameters)"

# Pattern for name attribute (accepts single or double quotes)
_NAME_ATTR = r"""name=["']([^"']+)["']"""

# Skip optional <reasoning>...</reasoning> tags
_REASONING_SKIP = r'(?:<reasoning>[\s\S]*?</reasoning>\s*)*'

# Primary regex: matches <tool_call name="..."><input>...</input></tool_call>
_TOOL_CALL_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*{_REASONING_SKIP}<{_INNER_TAG}>([\s\S]*?)</{_INNER_TAG}>\s*</tool_call>',
    re.DOTALL,
)

# Greedy variant: matches LAST </input></tool_call> instead of first
# Used when JSON content contains nested <tool_call>/<input> XML examples
_TOOL_CALL_GREEDY_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*{_REASONING_SKIP}<{_INNER_TAG}>([\s\S]*)</{_INNER_TAG}>\s*</tool_call>',
    re.DOTALL,
)

# Fallback regex: matches any single XML tag wrapping content
_TOOL_CALL_FALLBACK_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*{_REASONING_SKIP}<(\w+)>([\s\S]*?)</\2>\s*</tool_call>',
    re.DOTALL,
)

# Last-resort regex: NO inner tags - JSON directly inside <tool_call>
# Handles: <tool_call name="Read">{"file_path": "/path"}</tool_call>
_TOOL_CALL_BARE_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*([\s\S]*?)\s*</tool_call>',
    re.DOTALL,
)

# GLM format: <tool_call>ToolName<arg_key>value</arg_key><arg_value>value</arg_value></tool_call>
# \s* before name: handles newline/space between <tool_call> and name (GLM streaming artifact)
# [\w.-]+: allows dotted/dashed names (e.g. computer.bash, mcp-tool)
_TOOL_CALL_ARGKV_RE = re.compile(
    r'<tool_call>\s*([\w.-]+)((?:\s*<arg_key>[\s\S]*?</arg_key>\s*<arg_value>[\s\S]*?</arg_value>)+)\s*</tool_call>',
    re.DOTALL,
)

# Tolerant variant: accepts missing/truncated </tool_call> closing tag
# Used as last-resort recovery in flush() and extract_tool_calls_from_text()
# Only extracts pairs where BOTH <arg_key> and <arg_value> are present and complete
_TOOL_CALL_ARGKV_LOOSE_RE = re.compile(
    r'<tool_call>\s*([\w.-]+)((?:\s*<arg_key>[\s\S]*?</arg_key>\s*<arg_value>[\s\S]*?</arg_value>)+)\s*(?:</tool_call>|$)',
    re.DOTALL,
)

# GLM arg_key/arg_value pattern
_ARG_KV_PAIR_RE = re.compile(
    r'<arg_key>([\s\S]*?)</arg_key>\s*<arg_value>([\s\S]*?)</arg_value>',
    re.DOTALL,
)

# 5th fallback: diluted XML format after prompt dilution - models invent their own tags
# Handles: <tool_name>Read</tool_name><args>{"file_path": "..."}</args>
# or:      <tool_name>Read</tool_name><arguments>{"file_path": "..."}</arguments>
# NOTE: Opening and closing tag alternations are NOT backreferenced, so
# <args>content</arguments> intentionally matches - content extraction is priority
_TOOL_DILUTED_RE = re.compile(
    r'<tool_name>([\w]+)</tool_name>\s*<(?:args|arguments|params|input)>([\s\S]*?)</(?:args|arguments|params|input)>',
    re.DOTALL,
)

# 6th fallback: XML-as-tags format - model uses XML param tags instead of JSON
# Handles: <file_path>/path</file_path> <content>text</content>
_XML_PARAM_TAG_RE = re.compile(r'<(\w+)>([\s\S]*?)</\1>', re.DOTALL)

# 7th fallback: Attributed XML parameter format (Anthropic SDK style)
# Handles: <parameter name="file_path">/path</parameter>
_XML_ATTR_PARAM_RE = re.compile(
    r'<parameter\s+name=["\'](\w+)["\']\s*>([\s\S]*?)</parameter>',
    re.DOTALL,
)

# CDATA pattern
_CDATA_RE = re.compile(r'^<!\[CDATA\[([\s\S]*?)\]\]>$')

# Detects if opening  <tool_call has REAL (unescaped) quotes in name= attribute
# Used in _try_extract_tool to distinguish real tools from escaped examples
_REAL_NAME_RE = re.compile(r'<tool_call\s+name=["\'][^"\']+["\']')

# Opening  <tool_call tag
_TOOL_CALL_OPEN = "<tool_call"

# Closing </tool_call> tag
_TOOL_CALL_CLOSE = "</tool_call>"

# Partial tool call: name + whatever JSON we got
_PARTIAL_TOOL_RE = re.compile(
    r'<tool_call\s+' + _NAME_ATTR + r'\s*>\s*' + _REASONING_SKIP + r'<' + _INNER_TAG + r'>\s*([\s\S]*)',
    re.DOTALL,
)

# Partial argkv: <tool_call>Name<arg_key>value</arg_key>...(truncated)
_PARTIAL_ARGKV_RE = re.compile(
    r'<tool_call>(\w+)((?:\s*<arg_key>[\s\S]*?</arg_key>\s*<arg_value>[\s\S]*?</arg_value>)*[\s\S]*)',
    re.DOTALL,
)

# Partial XML-as-tags: <tool_call name="Write"><file_path>...</file_path><content>...(truncated)
_PARTIAL_XML_TAGS_RE = re.compile(
    r'<tool_call\s+' + _NAME_ATTR + r'\s*>\s*((?:<\w+>[\s\S]*?</\w+>\s*)*)',
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# DeepSeek-R1 / deepseek-reasoner native DSML format
# ---------------------------------------------------------------------------
# DeepSeek-R1 outputs tool calls using its internal DSML token separator format:
#   <｜DSML｜function_calls>
#     <｜DSML｜invoke name="ToolName">
#       <｜DSML｜parameter name="param1" string="true">value</｜DSML｜parameter>
#       <｜DSML｜parameter name="param2" string="false">{"key": "val"}</｜DSML｜parameter>
#     </｜DSML｜invoke>
#   </｜DSML｜function_calls>
#
# The ｜ separator is U+FF5C FULLWIDTH VERTICAL LINE.
# Character class [|\uff5c] matches both ASCII pipe and fullwidth pipe for robustness.

# Matches one complete <｜DSML｜invoke>...</｜DSML｜invoke> block
_DSML_INVOKE_RE = re.compile(
    r'<[|\uff5c]DSML[|\uff5c]invoke\s+name="([^"]+)">([\s\S]*?)</[|\uff5c]DSML[|\uff5c]invoke>',
    re.DOTALL,
)

# Matches one <｜DSML｜parameter> block inside an invoke
_DSML_PARAM_RE = re.compile(
    r'<[|\uff5c]DSML[|\uff5c]parameter\s+name="([^"]+)"[^>]*>([\s\S]*?)</[|\uff5c]DSML[|\uff5c]parameter>',
    re.DOTALL,
)

# String constants for fast buffer scanning (no regex overhead)
# Used for quick "is DSML present?" checks before running the full regex
_DSML_INVOKE_OPEN = "<\uff5cDSML\uff5cinvoke"     # <｜DSML｜invoke
_DSML_FCALLS_OPEN = "<\uff5cDSML\uff5cfunction_calls"  # <｜DSML｜function_calls
_DSML_INVOKE_CLOSE = "</\uff5cDSML\uff5cinvoke>"   # </｜DSML｜invoke>
_DSML_FCALLS_CLOSE = "</\uff5cDSML\uff5cfunction_calls>"  # </｜DSML｜function_calls>

# Export all patterns
__all__ = [
    '_INNER_TAG',
    '_NAME_ATTR',
    '_REASONING_SKIP',
    '_TOOL_CALL_RE',
    '_TOOL_CALL_GREEDY_RE',
    '_TOOL_CALL_FALLBACK_RE',
    '_TOOL_CALL_BARE_RE',
    '_TOOL_CALL_ARGKV_RE',
    '_TOOL_CALL_ARGKV_LOOSE_RE',
    '_ARG_KV_PAIR_RE',
    '_TOOL_DILUTED_RE',
    '_XML_PARAM_TAG_RE',
    '_XML_ATTR_PARAM_RE',
    '_CDATA_RE',
    '_REAL_NAME_RE',
    '_TOOL_CALL_OPEN',
    '_TOOL_CALL_CLOSE',
    '_PARTIAL_TOOL_RE',
    '_PARTIAL_ARGKV_RE',
    '_PARTIAL_XML_TAGS_RE',
    '_DSML_INVOKE_RE',
    '_DSML_PARAM_RE',
    '_DSML_INVOKE_OPEN',
    '_DSML_FCALLS_OPEN',
    '_DSML_INVOKE_CLOSE',
    '_DSML_FCALLS_CLOSE',
]
