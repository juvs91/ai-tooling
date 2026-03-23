---
name: semantic-code-search
description: Use when you need to search a large legacy codebase semantically — finding procedures, functions, or modules by business meaning rather than exact name. Invoke when keyword grep is insufficient because you know WHAT something does but not WHAT it's called.
---
# Semantic Code Search — MCP Pattern

This skill documents the **Semantic Code Search** pattern for large codebases using MCP (Model Context Protocol). Use it when keyword search is insufficient — when you need to find code by *business meaning*, not by exact name or keyword.

---

## When to Use This Pattern

- Codebase has thousands of stored procedures / functions / modules with opaque names
- You know the business concept ("price calculation", "inventory adjustment") but not the symbol name
- Grep returns too many or too few results
- Legacy codebase with no documentation — you need to find "where does X happen?"

---

## Architecture

The Semantic Code Search pipeline has 4 stages:

```
Codebase Objects
    │
    ▼
1. EXTRACTION          Extract all named objects (procedures, functions, classes, modules)
    │
    ▼
2. CHUNKING            Split into searchable chunks (~6KB max, respect logical boundaries)
    │
    ▼
3. EMBEDDING           Generate vector embeddings (e.g., text-embedding models, 768+ dims)
    │
    ▼
4. INDEX               Store in a vector database (BigQuery, pgvector, Chroma, Qdrant, etc.)
    │
    ▼
MCP Server             Expose search/lookup/dependencies as MCP tools
```

---

## MCP Tools to Expose

A well-designed Semantic Code Search MCP server exposes these tools:

| Tool | Input | Output |
|------|-------|--------|
| `search` | Business-language query string | Ranked list of matching code objects |
| `get_code` | Object ID / name | Full source code of the object |
| `dependencies` | Object ID | Objects this object calls |
| `impact` | Object ID | Objects that call this object (reverse deps) |
| `read_chunk` | Chunk ID | Raw chunk text for a partial object |

---

## Implementation Checklist

To build a Semantic Code Search MCP for your project:

1. **Extract**: Write an extraction script that dumps all named objects from your codebase
   - For SQL: `sql_procedure_analyzer.py` (already in `tools/software/discovery/`)
   - For Python: `structure_mapper.py` + `api_mapper.py`
   - For .NET/Java: use `decompiler_manager.py` to get source first

2. **Chunk**: Split by logical boundary (function/class/procedure), max ~6KB per chunk

3. **Embed**: Use any text embedding model
   - Cloud: Gemini `text-embedding-004`, OpenAI `text-embedding-3-small`, Cohere
   - Local: `sentence-transformers`, `nomic-embed-text`

4. **Index**: Store in a vector database
   - Cloud: BigQuery vector search, Pinecone, Weaviate
   - Local: SQLite with `sqlite-vec`, Chroma, Qdrant

5. **MCP Server**: Expose via FastMCP (Python) — see `.agents/skills/` for the `mcp-builder` skill

---

## Usage in Agent Workflows

When the Software Archeologist or Retro-Engineer needs to find relevant code:

```
1. Agent formulates a business-language query:
   "find all procedures that calculate minimum prices"

2. Call search(query) → returns ranked list of candidate objects

3. For top candidates: call get_code(id) → read full source

4. Call dependencies(id) → understand what it calls

5. Proceed with archaeology
```

---

## Project-Specific Configuration

If your project has a Semantic Code Search MCP deployed, add its configuration here:

```
MCP Server URL: <fill in>
Authentication: <fill in>
Index name / dataset: <fill in>
Embedding model: <fill in>
Rate limits: <fill in>
```

---

*To build a new Semantic Code Search MCP for this project, invoke `.agents/skills/core/tool-writer/SKILL.md` for the extraction/embedding pipeline and `.agents/skills/` `mcp-builder` for the server.*
