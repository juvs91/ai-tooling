"""
Session State — structured state preservation across compression boundaries.

Extracts entities, decisions, and phase checkpoints from conversation history
before compression removes old messages. Injects as PRESERVED_STATE: block
into the system prompt so the model (and Ralph) retain continuity.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class CheckpointInfo:
    phase: str
    status: str          # "complete" | "in_progress" | "pending"
    artifacts: list[str] = field(default_factory=list)
    completed_tasks: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "status": self.status,
            "artifacts": self.artifacts,
            "completed_tasks": self.completed_tasks,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CheckpointInfo":
        return cls(
            phase=d.get("phase", ""),
            status=d.get("status", "pending"),
            artifacts=d.get("artifacts", []),
            completed_tasks=d.get("completed_tasks", []),
            timestamp=d.get("timestamp", 0.0),
        )


@dataclass
class EntityInfo:
    name: str
    entity_type: str     # "file" | "table" | "function" | "server" | "other"
    context: str = ""    # short note on why it matters
    first_seen: float = field(default_factory=time.time)
    modified: bool = False  # True if file was written/edited (not just read)

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.entity_type, "context": self.context, "first_seen": self.first_seen, "modified": self.modified}

    @classmethod
    def from_dict(cls, d: dict) -> "EntityInfo":
        return cls(name=d["name"], entity_type=d.get("type", "other"), context=d.get("context", ""), first_seen=d.get("first_seen", 0.0), modified=d.get("modified", False))


@dataclass
class DecisionInfo:
    summary: str
    turn_index: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"summary": self.summary, "turn_index": self.turn_index, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionInfo":
        return cls(summary=d.get("summary", ""), turn_index=d.get("turn_index", 0), timestamp=d.get("timestamp", 0.0))


@dataclass
class TodoItem:
    content: str
    status: str      # "pending" | "in_progress" | "completed"
    priority: str = "medium"
    id: str = ""

    def to_dict(self) -> dict:
        return {"content": self.content, "status": self.status, "priority": self.priority, "id": self.id}

    @classmethod
    def from_dict(cls, d: dict) -> "TodoItem":
        return cls(content=d.get("content", ""), status=d.get("status", "pending"), priority=d.get("priority", "medium"), id=d.get("id", ""))


@dataclass
class SessionState:
    entities: dict[str, EntityInfo] = field(default_factory=dict)
    decisions: list[DecisionInfo] = field(default_factory=list)
    checkpoints: dict[str, CheckpointInfo] = field(default_factory=dict)
    todos: list[TodoItem] = field(default_factory=list)
    total_turns: int = 0
    total_compressions: int = 0
    start_time: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "entities": {k: v.to_dict() for k, v in self.entities.items()},
            "decisions": [d.to_dict() for d in self.decisions],
            "checkpoints": {k: v.to_dict() for k, v in self.checkpoints.items()},
            "todos": [t.to_dict() for t in self.todos],
            "total_turns": self.total_turns,
            "total_compressions": self.total_compressions,
            "start_time": self.start_time,
            "last_activity": self.last_activity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        return cls(
            entities={k: EntityInfo.from_dict(v) for k, v in d.get("entities", {}).items()},
            decisions=[DecisionInfo.from_dict(x) for x in d.get("decisions", [])],
            checkpoints={k: CheckpointInfo.from_dict(v) for k, v in d.get("checkpoints", {}).items()},
            todos=[TodoItem.from_dict(t) for t in d.get("todos", [])],
            total_turns=d.get("total_turns", 0),
            total_compressions=d.get("total_compressions", 0),
            start_time=d.get("start_time", 0.0),
            last_activity=d.get("last_activity", 0.0),
        )


# ── Extraction patterns ─────────────────────────────────────────────────────

_PHASE_RE = re.compile(
    r'(?i)(?:phase|fase)\s*(\d+)[\s:]*(?:[-–]?\s*)?'
    r'(complete|completed|done|✓|✅|in.progress|pending|skip)',
)
_CHECKBOX_DONE_RE = re.compile(r'\[x\]\s*(.+)', re.IGNORECASE)
_CHECKPOINT_MARKER_RE = re.compile(
    r'(?i)(?:checkpoint|milestone|phase[\s_]?complete)[:\s]+(.+)',
)
_FILE_PATH_RE = re.compile(r'((?:[\w./]+/)?[\w-]+\.(?:py|sql|json|md|sh|yaml|yml|txt|js|ts))', re.IGNORECASE)
_DECISION_RE = re.compile(
    r'(?i)(?:decidimos|we decided|approach:|decision:|we will use|going with|using)\s*:?\s*(.{10,120})',
)
_ARTIFACT_RE = re.compile(r'([\w-]+\.(?:md|json|txt|yaml|yml))', re.IGNORECASE)


def extract_session_state(messages: list[dict], existing: Optional[SessionState] = None) -> SessionState:
    """
    Extract structured state from conversation messages without an LLM call.

    Parses phase markers, checkboxes, file paths, and decisions using regex.
    Merges into existing state (for incremental updates across multiple compressions).
    """
    state = existing or SessionState()
    state.last_activity = time.time()
    state.total_compressions += 1

    for turn_idx, msg in enumerate(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        # Scan tool_use blocks for Edit/Write BEFORE text conversion (tool_use has no "text" field)
        if role == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                    continue
                inp = block.get("input") or {}
                fp = inp.get("file_path", "") if isinstance(inp, dict) else ""
                if fp:
                    if fp not in state.entities:
                        state.entities[fp] = EntityInfo(name=fp, entity_type="file", modified=True)
                    else:
                        state.entities[fp].modified = True
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )
        if not isinstance(content, str):
            content = str(content)

        # Phase / checkpoint extraction
        for m in _PHASE_RE.finditer(content):
            phase_num = m.group(1)
            status_raw = m.group(2).lower()
            status = "complete" if any(s in status_raw for s in ("complete", "done", "✓", "✅")) else (
                "in_progress" if "progress" in status_raw else "pending"
            )
            key = f"phase_{phase_num}"
            if key not in state.checkpoints or status == "complete":
                artifacts = _ARTIFACT_RE.findall(content)
                completed = _CHECKBOX_DONE_RE.findall(content)
                state.checkpoints[key] = CheckpointInfo(
                    phase=phase_num,
                    status=status,
                    artifacts=list(set(artifacts))[:10],
                    completed_tasks=[t.strip()[:80] for t in completed][:20],
                )

        # Decision extraction (assistant turns only — avoid extracting from tool output noise)
        if role == "assistant":
            for m in _DECISION_RE.finditer(content):
                summary = m.group(1).strip()[:120]
                if summary and not any(d.summary == summary for d in state.decisions):
                    state.decisions.append(DecisionInfo(summary=summary, turn_index=turn_idx))
                    if len(state.decisions) > 30:
                        state.decisions = state.decisions[-30:]

        # Entity extraction: file paths
        for path in _FILE_PATH_RE.findall(content):
            if path not in state.entities and len(state.entities) < 200:
                state.entities[path] = EntityInfo(name=path, entity_type="file")

    state.total_turns = max(state.total_turns, len(messages))
    return state


def extract_todo_state(messages: list) -> list[TodoItem]:
    """Scan messages in reverse for the most recent TodoWrite tool_use block.

    TodoWrite always emits the COMPLETE current todo list, so only the last
    invocation matters. Must scan full history including recent_messages (not
    just old_messages) to capture todos written after the last compression.
    """
    for msg in reversed(messages):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if role != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            name = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
            if btype != "tool_use" or name != "TodoWrite":
                continue
            inp = block.get("input") if isinstance(block, dict) else getattr(block, "input", None)
            todos_raw = (inp or {}).get("todos", []) if isinstance(inp, dict) else []
            return [
                TodoItem(
                    content=t.get("content", ""),
                    status=t.get("status", "pending"),
                    priority=t.get("priority", "medium"),
                    id=t.get("id", ""),
                )
                for t in todos_raw
                if isinstance(t, dict) and t.get("content")
            ]
    return []


def inject_state_into_system_prompt(system_content: str, state: SessionState) -> str:
    """
    Append a PRESERVED_STATE block to the system prompt.

    Generates a compact, human-readable state summary that helps the model
    avoid re-deriving information that was established in compressed turns.
    """
    if not state.checkpoints and not state.decisions and not state.entities and not state.todos:
        return system_content

    lines = ["\n\n--- PRESERVED_STATE ---"]

    if state.checkpoints:
        lines.append("## Phase Checkpoints")
        for key, cp in sorted(state.checkpoints.items()):
            symbol = "✅" if cp.status == "complete" else ("🔄" if cp.status == "in_progress" else "⏳")
            lines.append(f"  {symbol} Phase {cp.phase}: {cp.status}")
            if cp.artifacts:
                lines.append(f"    artifacts: {', '.join(cp.artifacts[:5])}")
            if cp.completed_tasks:
                lines.append(f"    completed: {len(cp.completed_tasks)} tasks")

    if state.todos:
        done    = [t for t in state.todos if t.status == "completed"]
        in_prog = [t for t in state.todos if t.status == "in_progress"]
        pending = [t for t in state.todos if t.status == "pending"]
        lines.append("## Active Tasks")
        for t in in_prog:
            lines.append(f"  🔄 IN_PROGRESS: {t.content}")
        for t in pending[:5]:
            lines.append(f"  ⏳ PENDING: {t.content}")
        if len(pending) > 5:
            lines.append(f"  ... and {len(pending) - 5} more pending")
        for t in done[-3:]:
            lines.append(f"  ✅ DONE: {t.content}")

    if state.decisions:
        lines.append("## Key Decisions")
        for d in state.decisions[-10:]:
            lines.append(f"  • {d.summary}")

    if state.entities:
        modified_paths = [name for name, e in state.entities.items() if e.entity_type == "file" and e.modified]
        read_only_paths = [name for name, e in state.entities.items() if e.entity_type == "file" and not e.modified]
        if modified_paths:
            lines.append(f"## Files Modified ({len(modified_paths)} total)")
            lines.append(f"  ✏️ {', '.join(modified_paths[:10])}")
            if len(modified_paths) > 10:
                lines.append(f"  ... and {len(modified_paths) - 10} more")
        if read_only_paths:
            lines.append(f"## Files Read ({len(read_only_paths)} total)")
            lines.append(f"  {', '.join(read_only_paths[:5])}")
            if len(read_only_paths) > 5:
                lines.append(f"  ... and {len(read_only_paths) - 5} more")

    lines.append(f"## Session Info")
    lines.append(f"  compressions={state.total_compressions}, turns_processed={state.total_turns}")
    lines.append("--- END PRESERVED_STATE ---")

    return system_content + "\n".join(lines)
