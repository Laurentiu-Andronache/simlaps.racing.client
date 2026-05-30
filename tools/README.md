# AST/RAG Tooling

This folder contains the repo-local scripts used to build, query, and serve a symbol-level AST index for the Python codebase.

## Files

- `build_ast_map.py`
  - Scans Python files and emits a JSONL symbol map.
  - Default output: `.windsurf/rag/ast_map.jsonl`
  - Also writes a manifest: `.windsurf/rag/ast_manifest.json`

- `build_ast_rag_index.py`
  - Loads the AST JSONL map into a local SQLite database.
  - Builds `nodes`, `edges`, and FTS5 search tables when available.
  - Default output: `.windsurf/rag/ast.db`

- `query_ast_rag.py`
  - Queries the local SQLite AST index from the command line.
  - Returns compact context blocks with `@file#line-line` anchors.

- `mcp_ast_rag_server.py`
  - Exposes the local AST/RAG index as a stdio MCP server for Windsurf.
  - Supports `ast_rag_search`, `ast_rag_symbol_lookup`, `ast_rag_stats`, and `ast_rag_refresh_index`.

## Outputs

Generated AST artifacts are written under:

- `.windsurf/rag/ast_map.jsonl`
- `.windsurf/rag/ast_manifest.json`
- `.windsurf/rag/ast.db`

These are generated files and should be refreshed after major code changes.

## Typical workflow

From repo root:

```powershell
python tools/build_ast_map.py --project-root . --roots src tests --out .windsurf/rag/ast_map.jsonl --manifest .windsurf/rag/ast_manifest.json
python tools/build_ast_rag_index.py --ast .windsurf/rag/ast_map.jsonl --db .windsurf/rag/ast.db
```

Query the index:

```powershell
python tools/query_ast_rag.py "where is lap validity decided" --db .windsurf/rag/ast.db --k 8
```

## MCP usage

Run the MCP server manually:

```powershell
python tools/mcp_ast_rag_server.py --repo-root . --db .windsurf/rag/ast.db
```

The workspace MCP config points Windsurf at this server through `.windsurf/mcp_config.json`.

## Refresh policy

Refresh the AST map and DB after:

- Major refactors
- File moves or module renames
- Large test additions
- Changes to public APIs you expect to search often

## Notes

- The index is intended for Python source in `src/` and `tests/`.
- `build_ast_map.py` skips common generated/cache directories.
- If FTS5 is unavailable in SQLite, search falls back to `LIKE` queries.
- The user workflow companion doc lives at `.windsurf/workflows/ast_rag.md`.
